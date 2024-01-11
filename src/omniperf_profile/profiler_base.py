##############################################################################bl
# MIT License
#
# Copyright (c) 2021 - 2023 Advanced Micro Devices, Inc. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
##############################################################################el

from abc import ABC, abstractmethod
import logging
import glob
import sys
import os
import re
from utils.utils import capture_subprocess_output, run_prof, gen_sysinfo, run_rocscope, error, demarcate
import config
import pandas as pd

class OmniProfiler_Base():
    def __init__(self, args, profiler_mode, soc):
        self.__args = args
        self.__profiler = profiler_mode
        self._soc = soc # OmniSoC obj
        
        self.__perfmon_dir = os.path.join(str(config.omniperf_home), "omniperf_soc", "profile_configs")

    def get_args(self):
        return self.__args
    def get_profiler_options(self, fname):
        """Fetch any version specific arguments required by profiler
        """
        # assume no SoC specific options and return empty list by default
        return []
    
    @demarcate
    def pmc_perf_split(self):
        """Avoid default rocprof join utility by spliting each line into a separate input file
        """
        workload_perfmon_dir = os.path.join(self.__args.path, "perfmon")
        lines = open(os.path.join(workload_perfmon_dir, "pmc_perf.txt"), "r").read().splitlines()

        # Iterate over each line in pmc_perf.txt
        mpattern = r"^pmc:(.*)"
        i = 0
        for line in lines:
            # Verify no comments
            stext = line.split("#")[0].strip()
            if not stext:
                continue

            # all pmc counters start with  "pmc:"
            m = re.match(mpattern, stext)
            if m is None:
                continue

            # Create separate file for each line
            fd = open(workload_perfmon_dir + "/pmc_perf_" + str(i) + ".txt", "w")
            fd.write(stext + "\n\n")
            fd.write("gpu:\n")
            fd.write("range:\n")
            fd.write("kernel:\n")
            fd.close()

            i += 1

        # Remove old pmc_perf.txt input from perfmon dir
        os.remove(workload_perfmon_dir + "/pmc_perf.txt")

    # joins disparate runs less dumbly than rocprof
    @demarcate
    def join_prof(self, output_headers, out=None):
        """Manually join separated rocprof runs
        """
        # Set default output directory if not specified
        if type(self.__args.path) == str:
            if out is None:
                out = self.__args.path + "/pmc_perf.csv"
            files = glob.glob(self.__args.path + "/" + "pmc_perf_*.csv")
        elif type(self.__args.path) == list:
            files = self.__args.path
        else:
            logging.error("ERROR: Invalid workload_dir")
            sys.exit(1)

        df = None
        for i, file in enumerate(files):
            _df = pd.read_csv(file) if type(self.__args.path) == str else file
            if self.__args.join_type == "kernel":
                key = _df.groupby(output_headers["Kernel_Name"]).cumcount()
                _df["key"] = _df.KernelName + " - " + key.astype(str)
            elif self.__args.join_type == "grid":
                key = _df.groupby([output_headers["Kernel_Name"], output_headers["Grid_Size"]]).cumcount()
                _df["key"] = (
                    _df[output_headers["Kernel_Name"]] + " - " + _df[output_headers["Grid_Size"]].astype(str) + " - " + key.astype(str)
                )
            else:
                print("ERROR: Unrecognized --join-type")
                sys.exit(1)

            if df is None:
                df = _df
            else:
                # join by unique index of kernel
                df = pd.merge(df, _df, how="inner", on="key", suffixes=("", f"_{i}"))

        # TODO: check for any mismatch in joins
        duplicate_cols = {
            output_headers["GPU_ID"]: [col for col in df.columns if output_headers["GPU_ID"] in col],
            output_headers["Grid_Size"]: [col for col in df.columns if output_headers["Grid_Size"] in col],
            output_headers["Workgroup_Size"]: [col for col in df.columns if output_headers["Workgroup_Size"] in col],
            output_headers["LDS_Per_Workgroup"]: [col for col in df.columns if output_headers["LDS_Per_Workgroup"] in col],
            output_headers["Scratch_Per_Workitem"]: [col for col in df.columns if output_headers["Scratch_Per_Workitem"] in col],
            output_headers["SGPR"]: [col for col in df.columns if output_headers["SGPR"] in col],
        }
        # Check for vgpr counter in ROCm < 5.3
        if "vgpr" in df.columns:
            duplicate_cols["vgpr"] = [col for col in df.columns if "vgpr" in col]
        # Check for vgpr counter in ROCm >= 5.3
        else:
            duplicate_cols[output_headers["Arch_VGPR"]] = [col for col in df.columns if output_headers["Arch_VGPR"] in col]
            duplicate_cols[output_headers["Accum_VGPR"]] = [col for col in df.columns if output_headers["Accum_VGPR"] in col]
        for key, cols in duplicate_cols.items():
            _df = df[cols]
            if not test_df_column_equality(_df):
                msg = (
                    "WARNING: Detected differing {} values while joining pmc_perf.csv".format(
                        key
                    )
                )
                logging.warning(msg + "\n")
            else:
                msg = "Successfully joined {} in pmc_perf.csv".format(key)
                logging.debug(msg + "\n")
            if test_df_column_equality(_df) and self.__args.verbose:
                logging.info(msg)

        # now, we can:
        #   A) throw away any of the "boring" duplicats
        df = df[
            [
                k
                for k in df.keys()
                if not any(
                    check in k
                    for check in [
                        # rocprofv1 headers
                        "gpu-id_",
                        "grd_",
                        "wgr_",
                        "lds_",
                        "scr_",
                        "vgpr_",
                        "sgpr_",
                        "Index_",
                        "queue-id",
                        "queue-index",
                        "pid",
                        "tid",
                        "fbar",
                        "sig",
                        "obj",
                        # rocprofv2 headers
                        "GPU_ID_",
                        "Grid_Size_",
                        "Workgroup_Size_",
                        "LDS_Per_Workgroup_",
                        "Scratch_Per_Workitem_",
                        "vgpr_",
                        "Arch_VGPR_",
                        "Accum_VGPR_",
                        "SGPR_",
                        "Dispatch_ID_",
                        "Queue_ID",
                        "Queue_Index",
                        "PID",
                        "TID",
                        "SIG",
                        "OBJ",
                        # rocscope specific merged counters, keep original
                        "dispatch_",
                    ]
                )
            ]
        ]
        #   B) any timestamps that are _not_ the duration, which is the one we care about
        df = df[
            [
                k
                for k in df.keys()
                if not any(
                    check in k
                    for check in [
                        "DispatchNs",
                        "CompleteNs",
                        # rocscope specific timestamp
                        "HostDuration",
                    ]
                )
            ]
        ]
        #   C) sanity check the name and key
        namekeys = [k for k in df.keys() if output_headers["Kernel_Name"] in k]
        assert len(namekeys)
        for k in namekeys[1:]:
            assert (df[namekeys[0]] == df[k]).all()
        df = df.drop(columns=namekeys[1:])
        # now take the median of the durations
        bkeys = []
        ekeys = []
        for k in df.keys():
            if output_headers["Start_Timestamp"] in k:
                bkeys.append(k)
            if output_headers["End_Timestamp"] in k:
                ekeys.append(k)
        # compute mean begin and end timestamps
        endNs = df[ekeys].mean(axis=1)
        beginNs = df[bkeys].mean(axis=1)
        # and replace
        df = df.drop(columns=bkeys)
        df = df.drop(columns=ekeys)
        df["BeginNs"] = beginNs
        df["EndNs"] = endNs
        # finally, join the drop key
        df = df.drop(columns=["key"])
        # save to file and delete old file(s), skip if we're being called outside of Omniperf
        if type(self.__args.path) == str:
            df.to_csv(out, index=False)
            if not self.__args.verbose:
                for file in files:
                    os.remove(file)
        else:
            return df

    #----------------------------------------------------
    # Required methods to be implemented by child classes
    #----------------------------------------------------
    @abstractmethod
    def pre_processing(self):
        """Perform any pre-processing steps prior to profiling.
        """
        logging.debug("[profiling] pre-processing using %s profiler" % self.__profiler)

        # verify not accessing parent directories
        if ".." in str(self.__args.path):
            error("Access denied. Cannot access parent directories in path (i.e. ../)")
        
        # verify correct formatting for application binary
        self.__args.remaining = self.__args.remaining[1:]
        if self.__args.remaining:
            if not os.path.isfile(self.__args.remaining[0]):
                error("Your command %s doesn't point to a executable. Please verify." % self.__args.remaining[0])
            self.__args.remaining = " ".join(self.__args.remaining)
        else:
            error("Profiling command required. Pass application executable after -- at the end of options.\n\t\ti.e. omniperf profile -n vcopy -- ./vcopy 1048576 256")
        
        # verify name meets MongoDB length requirements and no illegal chars
        if len(self.__args.name) > 35:
            error("-n/--name exceeds 35 character limit. Try again.")
        if self.__args.name.find(".") != -1 or self.__args.name.find("-") != -1:
            error("'-' and '.' are not permitted in -n/--name")

    @abstractmethod
    def run_profiling(self, version:str, prog:str):
        """Run profiling.
        """
        logging.debug("[profiling] performing profiling using %s profiler" % self.__profiler)
        
        # log basic info
        logging.info(str(prog) + " ver: " + str(version))
        logging.info("Path: " + str(os.path.abspath(self.__args.path)))
        logging.info("Target: " + str(self.__args.target))
        logging.info("Command: " + str(self.__args.remaining))
        logging.info("Kernel Selection: " + str(self.__args.kernel))
        logging.info("Dispatch Selection: " + str(self.__args.dispatch))
        if self.__args.ipblocks == None:
            logging.info("IP Blocks: All")
        else:
            logging.info("IP Blocks: "+ str(self.__args.ipblocks))
        if self.__args.kernel_verbose > 5:
            logging.info("KernelName verbose: DISABLED")
        else:
            logging.info("KernelName verbose: " + str(self.__args.kernel_verbose))

        # Run profiling on each input file
        for fname in glob.glob(self.get_args().path + "/perfmon/*.txt"):
            # Kernel filtering (in-place replacement)
            if not self.__args.kernel == None:
                success, output = capture_subprocess_output(
                    [
                        "sed",
                        "-i",
                        "-r",
                        "s%^(kernel:).*%" + "kernel: " + ",".join(self.__args.kernel) + "%g",
                        fname,
                    ]
                )
                # log output from profile filtering
                if not success:
                    error(output)
                else:
                    logging.debug(output)

            # Dispatch filtering (inplace replacement)
            if not self.__args.dispatch == None:
                success, output = capture_subprocess_output(
                    [
                        "sed",
                        "-i",
                        "-r",
                        "s%^(range:).*%" + "range: " + " ".join(self.__args.dispatch) + "%g",
                        fname,
                    ]
                )
                # log output from profile filtering
                if not success:
                    error(output)
                else:
                    logging.debug(output)
            logging.info("\nCurrent input file: %s" % fname)
            
            # Fetch any SoC/profiler specific profiling options
            options = self._soc.get_profiler_options()
            options += self.get_profiler_options(fname)

            if self.__profiler == "rocprofv1" or self.__profiler == "rocprofv2":
                run_prof(
                    fname=fname, 
                    # workload_dir=self.get_args().path, 
                    # perfmon_dir=self.__perfmon_dir, 
                    # cmd=self.__args.remaining,
                    # target=self.__args.target,
                    profiler_options=options
                )

            elif self.__profiler == "rocscope":
                run_rocscope(self.__args, fname)
            else:
                #TODO: Finish logic
                error("profiler not supported")

    @abstractmethod
    def post_processing(self):
        """Perform any post-processing steps prior to profiling.
        """
        logging.debug("[profiling] performing post-processing using %s profiler" % self.__profiler)
        gen_sysinfo(self.__args.name, self.get_args().path, self.__args.ipblocks, self.__args.remaining, self.__args.no_roof)

def test_df_column_equality(df):
    return df.eq(df.iloc[:, 0], axis=0).all(1).all()
