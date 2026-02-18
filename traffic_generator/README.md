# Traffic Generator

Scripts to generate GTPU traffic.
Deploys a traffic generation daemon on the remote host which is connected to the Tofino switch.

## Quickstart
```bash
cd traffic_generator
./build.sh
./deploy.sh 

./deploy.sh start  # start traffic generation on remote host
./deploy.sh stop   # stop traffic generation on remote host

./deploy.sh status # check status of traffic generation on remote host
./deploy.sh logs  # check logs of traffic generation on remote host
```