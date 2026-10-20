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
    for block in range(repeat):
        for i in range(rnd.randint(5, 12)):
            add(f"[SORTER          ]T:  Transaction {rnd.randint(10**6, 10**7)} committed for table 'SALES'.'ORDERS' (sorter.c:{rnd.randint(100, 900)})")
        add(f"[TARGET_APPLY    ]T:  Going to start applying bulk changes (bulk_apply.c:400)")
        add(f"[TARGET_APPLY    ]T:  Bulk finished. (bulk_apply.c:480)")
        if block % 20 == 0:
            lat = 2 + block / 15 + rnd.random() * 3
            add(f"[PERFORMANCE     ]T:  Source latency {lat * 0.2:.2f} seconds, Target latency {lat:.2f} seconds, "
                f"Handling latency {lat * 0.1:.2f} seconds (sorter_transaction.c:200)")
        if block % 45 == 7:
            add("[TARGET_APPLY    ]W:  Applying INSERTs one-by-one for table 'SALES'.'ORDER_LINES' (bulk_apply.c:1210)")
        if block % 60 == 13:
            add("[TARGET_APPLY    ]E:  Failed to execute statement on host_xyz. RetCode: SQL_ERROR  SqlState: 57014 NativeError: 604 "
                "Message: Statement reached its statement or warehouse timeout of 3600 second(s) (ar_odbc_stmt.c:2800)")
            lines.append("    Statement: MERGE INTO \"SALES\".\"ORDERS\" T USING ...")
        if block % 110 == 50:
            add("[SOURCE_CAPTURE  ]E:  ORA-03113: end-of-file on communication channel to orarac02.contoso.local (oracle_endpoint.c:900)")
            add("[SOURCE_CAPTURE  ]I:  Trying to reconnect to the source (oracle_endpoint.c:910)")
    add("[TASK_MANAGER    ]I:  Task is stopped by user request (replicationtask.c:2000)")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
