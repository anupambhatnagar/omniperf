# -----------------------------------------------------------------------
# NOTE:
# Dependencies are not included as part of Omniperf.
# It's the user's responsibility to accept any licensing implications
# before building the project
# -----------------------------------------------------------------------

FROM --platform=linux/amd64 ubuntu:22.04
WORKDIR /app

USER root
ENV DEBIAN_FRONTEND noninteractive

ADD grafana_plugins/svg_plugin /var/lib/grafana/plugins/custom-svg
ADD grafana_plugins/omniperfData_plugin /var/lib/grafana/plugins/omniperfData_plugin

RUN apt-get update && \
    apt-get install -y apt-transport-https software-properties-common adduser libfontconfig1 wget curl gnupg && \
    wget https://dl.grafana.com/enterprise/release/grafana-enterprise_8.3.4_amd64.deb &&\
    dpkg -i grafana-enterprise_8.3.4_amd64.deb &&\
    echo "deb https://packages.grafana.com/enterprise/deb stable main" | tee -a /etc/apt/sources.list.d/grafana.list && \
    echo "deb [signed-by=/usr/share/keyrings/yarnkey.gpg] https://dl.yarnpkg.com/debian stable main" | tee /etc/apt/sources.list.d/yarn.list

RUN curl -fsSL https://pgp.mongodb.com/server-7.0.asc | gpg -o /usr/share/keyrings/mongodb-server-7.0.gpg --dearmor
RUN echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/7.0 multiverse" | tee /etc/apt/sources.list.d/mongodb-org-7.0.list
RUN wget -q -O - https://packages.grafana.com/gpg.key | apt-key add -
RUN curl -sL https://dl.yarnpkg.com/debian/pubkey.gpg | gpg --dearmor | tee /usr/share/keyrings/yarnkey.gpg > /dev/null

RUN curl -fsSL https://deb.nodesource.com/setup_lts.x | bash -
RUN dpkg --remove --force-remove-reinstreq libnode-dev
RUN dpkg --remove --force-remove-reinstreq libnode72:amd64
RUN apt install -y yarn nodejs

RUN apt-get update                                                      && \
    apt-get install -y mongodb-org                                      && \
    apt-get install -y tzdata systemd apt-utils vim net-tools

RUN mkdir -p /nonexistent                                               && \
    /usr/sbin/grafana-cli plugins install michaeldmoore-multistat-panel && \
    /usr/sbin/grafana-cli plugins install ae3e-plotly-panel             && \
    /usr/sbin/grafana-cli plugins install natel-plotly-panel            && \
    /usr/sbin/grafana-cli plugins install grafana-image-renderer

RUN chown root:grafana /etc/grafana
WORKDIR /var/lib/grafana/plugins/omniperfData_plugin
RUN npm install
RUN npm run build
RUN apt-get autoremove -y
RUN apt-get autoclean -y
WORKDIR /var/lib/grafana/plugins/custom-svg
RUN yarn install
RUN yarn build
RUN yarn autoclean
RUN sed -i "s/  bindIp.*/  bindIp: 0.0.0.0/" /etc/mongod.conf
RUN  mkdir -p /var/lib/grafana						                              && \
    touch /var/lib/grafana/grafana.lib					                        && \
    chown grafana:grafana /var/lib/grafana/grafana.lib
RUN rm /app/grafana-enterprise_8.3.4_amd64.deb

# Overwrite grafana ini file
COPY docker/grafana.ini /etc/grafana

# switch Grafana port to 4000
RUN sed -i "s/^;http_port = 3000/http_port = 4000/" /etc/grafana/grafana.ini && \
    sed -i "s/^http_port = 3000/http_port = 4000/" /usr/share/grafana/conf/defaults.ini

# starts mongo and grafana-server at startup
COPY docker/docker-entrypoint.sh /docker-entrypoint.sh

ENTRYPOINT [ "/docker-entrypoint.sh" ]
