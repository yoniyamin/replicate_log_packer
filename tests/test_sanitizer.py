from logpacker.sanitize import Sanitizer, SanitizeOptions


def clean(line: str, **opts) -> str:
    return Sanitizer(SanitizeOptions(**opts)).apply(line)


def test_connection_string_values_are_masked():
    out = clean("Connecting with SERVER=ORAPRD;UID=repl_admin;PWD=S3cr3t!;PORT=1521")
    assert "ORAPRD" not in out and "repl_admin" not in out and "S3cr3t" not in out
    assert "SERVER=host_01" in out and "UID=user_01" in out and "PWD=[REDACTED]" in out
    assert "PORT=1521" in out


def test_same_value_gets_same_token_and_is_learned():
    s = Sanitizer()
    first = s.apply("Connecting with SERVER=ORAPRD;")
    later = s.apply("Lost connection to ORAPRD, retrying")
    assert "host_01" in first and "host_01" in later and "ORAPRD" not in later


def test_header_host_email_ip_license_and_user_path():
    s = Sanitizer()
    out = s.apply("Task Server Log - TASK1 (V2025.5.0.247 dbprod01.contoso.com Microsoft Windows, PID: 1)")
    assert "dbprod01" not in out and "V2025.5.0.247" in out and "TASK1" in out
    assert "contoso" not in s.apply("mail to dba.team@contoso.com")
    assert "10.20.30.40" not in s.apply("Connected to 10.20.30.40 ok")
    assert "Contoso" not in s.apply("Licensed to Contoso Ltd, license expires")
    assert "jsmith" not in s.apply(r"folder C:\Users\jsmith\AppData")


def test_username_after_space_and_ezconnect_service():
    s = Sanitizer()
    out = s.apply("Going to connect to Oracle server //10.1.2.3:1521/CBS3CDC with username ETLUSR_CDC (x.c:1)")
    assert "CBS3CDC" not in out and "ETLUSR_CDC" not in out and "10.1.2.3" not in out
    assert "//ip_01:1521/db_01" in out and "username user_01" in out
    url = s.apply("Calling https://api.example.com/v1/items")
    assert "/v1/items" in url and "example.com" not in url


def test_technical_tokens_are_left_alone():
    line = ("00004120: 2026-09-14T08:00:00 [TARGET_APPLY    ]E:  RetCode: SQL_ERROR SqlState: HY000 "
            "NativeError: 1205 ORA-00054 table 'dbo'.'orders' (ar_odbc_stmt.c:2800) version 7.0.0")
    assert clean(line) == line


def test_objects_and_custom_terms_are_optional():
    line = "Start applying for table 'SALES'.'ORDERS' at Fabrikam"
    assert clean(line) == line
    out = clean(line, mask_objects=True, custom_terms=["Fabrikam"])
    assert "'obj_01'.'obj_02'" in out and "term_01" in out


def test_sql_server_host_and_database_names():
    s = Sanitizer()
    out = s.apply("[TARGET_LOAD     ]I:  Going to connect to server 'SQLHOST77\\INST01' database 'salesdb_qa'  (x.c:1)")
    assert out == "[TARGET_LOAD     ]I:  Going to connect to server 'host_01\\INST01' database 'db_01'  (x.c:1)"
    odbc = s.apply("ODBC connection string: DRIVER={Adaptive Server Enterprise};SERVER=10.9.8.7;port=10000;"
                   "UID=repl;DB=salesdb_qa;textSize=2147483647;")
    assert "SERVER=ip_01" in odbc and "DB=db_01" in odbc and "salesdb_qa" not in odbc  # same db, same token
    assert s.apply("Server=h;Database=master;Initial Catalog=crm_prod;").endswith("Database=master;Initial Catalog=db_02;")
    assert "SQLHOST77" not in s.apply("Lost connection to SQLHOST77")  # learned
    keep = "Target endpoint 'SQL Server' is using provider syntax 'SQLServer'  (x.c:1)"
    assert s.apply(keep) == keep


def test_versions_are_not_ip_addresses():
    for line in ("Sybase DB version '16.0.3.11'  (sybase_endpoint_imp.c:936)",
                 "ODBC driver version: '16.0.03.11'  (ar_odbc_conn.c:633)",
                 "Oracle Client version: 19.3.0.0  (oracle_endpoint_conn.c:624)"):
        assert clean(line) == line


def test_oracle_descriptor_structure_is_kept():
    out = clean("Going to connect to Oracle server (DESCRIPTION = (ADDRESS = (PROTOCOL = TCP)(HOST = orahost9)"
                "(PORT = 1522)) (CONNECT_DATA = (SERVER = DEDICATED) (SERVICE_NAME = fvsvc01))) with username etl")
    assert "(PROTOCOL = TCP)" in out and "(SERVER = DEDICATED)" in out and "(PORT = 1522)" in out
    assert "orahost9" not in out and "fvsvc01" not in out and "SERVICE_NAME = db_01" in out


def test_disabled_is_a_no_op():
    line = "SERVER=ORAPRD;PWD=x"
    assert clean(line, enabled=False) == line
