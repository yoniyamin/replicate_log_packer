"""Generate a synthetic Replicate task log with PII, errors, latency and noise.

    uv run python tools/make_sample_log.py out.log --repeat 2000
"""

from __future__ import annotations

import argparse
import random
from datetime import datetime, timedelta
from pathlib import Path

HEADER = [
    "[TASK_MANAGER    ]I:  Task Server Log - SALES_TO_SNOW (V2025.5.0.247 dbprod01.contoso.com Microsoft Windows Server 2019 64-bit, PID: 4120) (at_logger.c:2770)",
    "[TASK_MANAGER    ]I:  Licensed to Contoso Ltd, license expires 2027-01-01 (license.c:120)",
    "[TASK_MANAGER    ]I:  Task 'SALES_TO_SNOW' running full load and CDC in fresh start mode (replicationtask.c:1300)",
    "[SOURCE_CAPTURE  ]I:  Source endpoint 'Oracle' is using provider syntax 'Oracle' (provider.c:200)",
    "[TARGET_APPLY    ]I:  Target endpoint 'Snowflake' is using provider syntax 'Snowflake' (provider.c:200)",
    "[SOURCE_CAPTURE  ]I:  Connecting with SERVER=ORAPRD;UID=repl_admin;PWD=S3cr3t!;PORT=1521 (oracle_endpoint.c:88)",
    "[TARGET_APPLY    ]I:  Connected to 10.20.30.40 as user=etl_svc (odbc_endpoint.c:301)",
    "[TARGET_APPLY    ]I:  Set Bulk Timeout = 30000 milliseconds (bulk_apply.c:90)",
    "[TARGET_APPLY    ]I:  Set Bulk Timeout Min = 1000 milliseconds (bulk_apply.c:91)",
    "[TARGET_APPLY    ]I:  Parallel bulk apply enabled with maximum 4 apply threads (bulk_apply.c:95)",
    "[UTILITIES       ]I:  Notification mail will be sent to dba.team@contoso.com (notif.c:12)",
    "[INFRASTRUCTURE  ]I:  Using data folder C:\\Users\\jsmith\\AppData\\Replicate\\data (infra.c:44)",
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--repeat", type=int, default=300, help="noise blocks to generate")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    write_sample(args.out, args.repeat, args.seed)
    print(f"wrote {args.out} ({args.out.stat().st_size:,} bytes)")


def write_sample(out: Path, repeat: int = 300, seed: int = 7) -> None:
    rnd = random.Random(seed)
    ts = datetime(2026, 9, 14, 8, 0, 0)
    lines: list[str] = []

    def add(body: str, thread: int = 4120) -> None:
        nonlocal ts
        ts += timedelta(seconds=rnd.choice((0, 0, 1)))
        lines.append(f"{thread:08d}: {ts:%Y-%m-%dT%H:%M:%S}:{rnd.randint(0, 999999):06d} {body}")

    for body in HEADER:
        add(body)
    add("[INFRASTRUCTURE  ]I:  The log level for 'TARGET_APPLY' has been changed from 'INFO' to 'TRACE'.  (at_logger.c:3018)")
    _full_load(add, rnd)
    add("[TASK_MANAGER    ]I:  Starting replication now  (replicationtask.c:3599)")
    for block in range(repeat):
        for i in range(rnd.randint(5, 12)):
            add(f"[SORTER          ]T:  Transaction {rnd.randint(10**6, 10**7)} committed for table 'SALES'.'ORDERS' (sorter.c:{rnd.randint(100, 900)})")
        _batch(add, rnd, block)
        if block % 20 == 0:
            lat = 2 + block / 15 + rnd.random() * 3
            add(f"[PERFORMANCE     ]T:  Source latency {lat * 0.2:.2f} seconds, Target latency {lat:.2f} seconds, "
                f"Handling latency {lat * 0.1:.2f} seconds (sorter_transaction.c:200)")
        if block % 45 == 7:
            add("[TARGET_APPLY    ]W:  Applying INSERTs one-by-one for table 'SALES'.'ORDER_LINES' (bulk_apply.c:1210)")
            add("[TARGET_APPLY    ]I:  Switch back to bulk apply mode  (bulk_apply.c:5266)")
        if block % 60 == 13:
            add("[TARGET_APPLY    ]E:  Failed to execute statement on host_xyz. RetCode: SQL_ERROR  SqlState: 57014 NativeError: 604 "
                "Message: Statement reached its statement or warehouse timeout of 3600 second(s) (ar_odbc_stmt.c:2800)")
            lines.append("    Statement: MERGE INTO \"SALES\".\"ORDERS\" T USING ...")
        if block % 110 == 50:
            add("[SOURCE_CAPTURE  ]E:  ORA-03113: end-of-file on communication channel to orarac02.contoso.local (oracle_endpoint.c:900)")
            add("[SOURCE_CAPTURE  ]I:  Trying to reconnect to the source (oracle_endpoint.c:910)")
    add("[TASK_MANAGER    ]I:  Task is stopped by user request (replicationtask.c:2000)")
    add("[AT_GLOBAL       ]I:  Closing log file at Mon Sep 14 18:00:00 2026  (at_logger.c:2615)")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _batch(add, rnd, block: int) -> None:
    """One CDC batch in the 'reason first' ordering, with a staged file upload."""
    reason = "stream timeout" if block % 3 else "memory limit"
    add(f"[TARGET_APPLY    ]T:  Finish bulk because {reason}  (bulk_apply.c:1051)")
    add("[TARGET_APPLY    ]T:  Bulk finished. First record 1. Last Record Id 9  (bulk_apply.c:2277)")
    add("[TARGET_APPLY    ]T:  Going to start applying bulk changes for table '1'  (bulk_apply.c:6125)")
    n = rnd.randint(1, 5000)
    add("[TARGET_APPLY    ]T:  Start applying of 'INSERT (1)' events for table 'SALES'.'ORDERS' (1).  (bulk_apply.c:2929)")
    add(f"[TARGET_APPLY    ]T:  Going to run insert statement, from seq 1 to seq {n}, statement 'INSERT ...'  (bulk_apply.c:3000)")
    name = f"CDC{block:08X}.csv"
    add(f"[TARGET_APPLY    ]T:  going to compress file 'D:\\data\\tasks\\SALES\\{name}' to 'D:\\data\\tasks\\SALES\\{name}.gz'  (csv_target.c:10)")
    add("[TARGET_APPLY    ]T:  file compressed  (csv_target.c:20)")
    add(f"[TARGET_APPLY    ]T:  File {name}.gz of size {n * 120} was uploaded successfully  (cloud_imp.c:50)")
    add(f"[TARGET_APPLY    ]T:  Finished applying of {n} 'INSERT (1)' events for table 'SALES'.'ORDERS' (1).  (bulk_apply.c:3120)")
    add("[TARGET_APPLY    ]T:  Finished applying bulk changes for tables  (bulk_apply.c:6199)")


def _full_load(add, rnd) -> None:
    """Two tables: one plain, one segmented with a reload of segment 2."""
    add("[TASK_MANAGER    ]I:  Start initializing table 'SALES'.'CUSTOMERS' (Id = 1) by subtask 1  (replicationtask.c:10)")
    add("[TASK_MANAGER    ]I:  Start loading table 'SALES'.'CUSTOMERS' (Id = 1) by subtask 1  (replicationtask.c:11)")
    add("[SOURCE_UNLOAD   ]I:  Unload finished for table 'SALES'.'CUSTOMERS' (Id = 1). 5000 rows sent.  (unload.c:5)")
    add("[TARGET_LOAD     ]I:  Load finished for table 'SALES'.'CUSTOMERS' (Id = 1). 5000 rows received. 0 rows skipped. Volume transferred 640000.  (load.c:5)")
    add("[TASK_MANAGER    ]I:  Start initializing segmented table 'SALES'.'ORDERS' (Id = 2) by subtask 2  (replicationtask.c:10)")
    for seg in (1, 2):
        add(f"[TASK_MANAGER    ]I:  Start loading segment #{seg} of 2 of table 'SALES'.'ORDERS' (Id = 2) by subtask {seg + 1}  (replicationtask.c:12)")
    add("[SOURCE_UNLOAD   ]I:  Unload finished for segment #1 of segmented table 'SALES'.'ORDERS' (Id = 2). 7000 rows sent.  (unload.c:6)")
    add("[TARGET_LOAD     ]I:  Load finished for segment #1 of segmented table 'SALES'.'ORDERS' (Id = 2). 7000 rows received. 0 rows skipped. Volume transferred 900000.  (load.c:6)")
    add("[TASK_MANAGER    ]E:  Reloading table 2 because subtask #3 that was loading segment 2 finished with error  (replicationtask.c:20)")
    for seg in (1, 2):
        add(f"[TASK_MANAGER    ]I:  Start loading segment #{seg} of 2 of table 'SALES'.'ORDERS' (Id = 2) by subtask {seg + 1}  (replicationtask.c:12)")
        add(f"[SOURCE_UNLOAD   ]I:  Unload finished for segment #{seg} of segmented table 'SALES'.'ORDERS' (Id = 2). 7000 rows sent.  (unload.c:6)")
        add(f"[TARGET_LOAD     ]I:  Load finished for segment #{seg} of segmented table 'SALES'.'ORDERS' (Id = 2). 7000 rows received. 0 rows skipped. Volume transferred 900000.  (load.c:6)")
    add("[TASK_MANAGER    ]I:  Full load completed  (replicationtask.c:30)")


if __name__ == "__main__":
    main()
