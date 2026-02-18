#!/usr/bin/env python3
# Configure policing for meter colors:
# 0 -> count only, 1/2 -> queue 7 (best-effort), 3 -> drop

import logging
from bfrt.controller import Controller

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

def main():
    c = Controller()
    try:
        c.setup_tables(["Ingress.QoSMeter.qos_table"])
        logging.info("Installing entries for Ingress.QoSMeter.qos_table")

        entries = [
            ([("meta.bridged_md.meter_color", 0)], "Ingress.QoSMeter.count_drop", []),
            ([("meta.bridged_md.meter_color", 1)], "Ingress.QoSMeter.set_queue", [("qid", 7)]),
            ([("meta.bridged_md.meter_color", 2)], "Ingress.QoSMeter.set_queue", [("qid", 7)]),
            ([("meta.bridged_md.meter_color", 3)], "Ingress.QoSMeter.drop", []),
        ]

        c.program_table("Ingress.QoSMeter.qos_table", entries)
        logging.info("Done.")
    finally:
        c.tear_down()

if __name__ == "__main__":
    main()
