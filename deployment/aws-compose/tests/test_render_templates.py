import pathlib
import re
import subprocess
import sys
import tempfile
import unittest

from deployment.aws_compose_renderer import render_text


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
COMPOSE_DIR = REPO_ROOT / "deployment" / "aws-compose"
SOURCE_SCHEMA = REPO_ROOT / "sql" / "v4.2.0" / "maxkey.sql"
HARDENING_TEMPLATE = COMPOSE_DIR / "mysql" / "init" / "01-hardening.sql.template"
RENDER_SCRIPT = COMPOSE_DIR / "scripts" / "render_templates.py"
MANAGEMENT_APP_ID = "622076759805923328"


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def rendered_hardening_sql() -> str:
    return render_text(
        HARDENING_TEMPLATE.read_text(encoding="utf-8"),
        {
            "MAXKEY_ADMIN_BCRYPT": "$2a$10$abcdefghijklmnopqrstuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu",
            "PUBLIC_BASE_URL": "https://login.example.test",
        },
    )


def source_schema() -> str:
    return SOURCE_SCHEMA.read_text(encoding="utf-8", errors="ignore")


def table_columns(table_name: str) -> dict[str, str]:
    match = re.search(
        rf"CREATE TABLE `{re.escape(table_name)}` \((.*?)\n\) ENGINE=",
        source_schema(),
        re.DOTALL,
    )
    if not match:
        raise AssertionError(f"source schema missing table {table_name}")

    columns = {}
    for line in match.group(1).splitlines():
        column = re.match(r"\s+`([^`]+)`\s+(.+)", line)
        if column:
            columns[column.group(1)] = column.group(2)
    return columns


class RenderTextTest(unittest.TestCase):
    def test_replaces_all_declared_values(self):
        self.assertEqual(
            render_text("__PUBLIC_BASE_URL__/x", {"PUBLIC_BASE_URL": "http://10.0.0.5"}),
            "http://10.0.0.5/x",
        )

    def test_rejects_unresolved_placeholder(self):
        with self.assertRaisesRegex(ValueError, "MISSING"):
            render_text("__MISSING__", {})

    def test_rejects_placeholder_left_by_replacement_value(self):
        with self.assertRaisesRegex(ValueError, "still contains placeholders"):
            render_text("__OUTER__", {"OUTER": "__INNER__"})

    def test_preserves_dollar_characters_in_values(self):
        bcrypt = "$2a$10$abcdefghijklmnopqrstuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu"
        self.assertEqual(render_text("__MAXKEY_ADMIN_BCRYPT__", {"MAXKEY_ADMIN_BCRYPT": bcrypt}), bcrypt)


class HardeningSqlTemplateTest(unittest.TestCase):
    def test_rendered_sql_contains_bcrypt_prefix_and_no_placeholders(self):
        sql = rendered_hardening_sql()

        self.assertIn("'{bcrypt}$2a$10$", sql)
        self.assertIsNone(re.search(r"__[A-Z0-9_]+__", sql))

    def test_admin_password_and_login_state_are_hardened(self):
        sql = normalize_sql(rendered_hardening_sql())

        self.assertIn(
            "UPDATE mxk_userinfo SET PASSWORD = "
            "'{bcrypt}$2a$10$abcdefghijklmnopqrstuuuuuuuuuuuuuuuuuuuuuuuuuuuuuuu', "
            "DECIPHERABLE = '', PASSWORDSETTYPE = 1, STATUS = 1, ISLOCKED = 1, "
            "BADPASSWORDCOUNT = 0 WHERE ID = '1';",
            sql,
        )

    def test_non_admin_seeded_users_are_disabled(self):
        sql = normalize_sql(rendered_hardening_sql())

        self.assertIn("UPDATE mxk_userinfo SET STATUS = 2 WHERE ID <> '1';", sql)

    def test_only_management_application_remains_active(self):
        sql = normalize_sql(rendered_hardening_sql())

        self.assertIn(
            f"UPDATE mxk_apps SET STATUS = CASE WHEN ID = '{MANAGEMENT_APP_ID}' THEN 1 ELSE 0 END;",
            sql,
        )

    def test_management_application_urls_are_rewritten(self):
        sql = normalize_sql(rendered_hardening_sql())

        self.assertIn(
            f"UPDATE mxk_apps SET LOGINURL = 'https://login.example.test/maxkey-mgt' "
            f"WHERE ID = '{MANAGEMENT_APP_ID}';",
            sql,
        )
        self.assertIn(
            f"UPDATE mxk_apps_cas_details SET SERVICE = "
            f"'https://login.example.test/maxkey-mgt/passport/trust/auth', CALLBACKURL = "
            f"'https://login.example.test/maxkey-mgt/passport/trust/auth' "
            f"WHERE ID = '{MANAGEMENT_APP_ID}';",
            sql,
        )

    def test_completion_marker_is_inserted_after_hardening_statements(self):
        sql = rendered_hardening_sql()
        marker = sql.index("VALUES ('initial_hardening', 'complete')")

        for statement in (
            "UPDATE mxk_userinfo\nSET PASSWORD",
            "UPDATE mxk_userinfo SET STATUS = 2",
            "UPDATE mxk_apps\nSET STATUS",
            "UPDATE mxk_apps\nSET LOGINURL",
            "UPDATE mxk_apps_cas_details\nSET SERVICE",
        ):
            self.assertLess(sql.index(statement), marker)

    def test_hardening_identifiers_and_seed_values_exist_in_source_schema(self):
        userinfo = table_columns("mxk_userinfo")
        apps = table_columns("mxk_apps")
        cas_details = table_columns("mxk_apps_cas_details")
        schema = source_schema()

        self.assertGreaterEqual(
            set(userinfo),
            {"ID", "PASSWORD", "DECIPHERABLE", "PASSWORDSETTYPE", "STATUS", "ISLOCKED", "BADPASSWORDCOUNT"},
        )
        self.assertGreaterEqual(set(apps), {"ID", "LOGINURL", "STATUS"})
        self.assertGreaterEqual(set(cas_details), {"ID", "SERVICE", "CALLBACKURL"})
        self.assertRegex(userinfo["STATUS"], r"tinyint unsigned")
        self.assertRegex(apps["STATUS"], r"tinyint unsigned")
        self.assertIn("INSERT INTO `mxk_userinfo` VALUES ('1','admin'", schema)
        self.assertIn(f"INSERT INTO `mxk_apps` VALUES ('{MANAGEMENT_APP_ID}'", schema)
        self.assertIn(f"INSERT INTO `mxk_apps_cas_details` VALUES ('{MANAGEMENT_APP_ID}'", schema)


class RenderTemplatesCliTest(unittest.TestCase):
    def test_cli_renders_requested_files_without_reimplementing_placeholder_logic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            source_sql = root / "maxkey.sql.template"
            hardening_template = root / "01-hardening.sql.template"
            env_template = root / ".env.template"
            nginx_template = root / "maxkey.conf.template"
            output_root = root / "srv" / "maxkey"
            compose_output = root / "compose"

            source_sql.write_text("database=__DATABASE_NAME__\n", encoding="utf-8")
            hardening_template.write_text("hash=__MAXKEY_ADMIN_BCRYPT__\n", encoding="utf-8")
            env_template.write_text("PUBLIC_BASE_URL=__PUBLIC_BASE_URL__\n", encoding="utf-8")
            nginx_template.write_text("proxy_pass __PUBLIC_BASE_URL__;\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    str(RENDER_SCRIPT),
                    "--source-sql",
                    str(source_sql),
                    "--hardening-template",
                    str(hardening_template),
                    "--env-template",
                    str(env_template),
                    "--nginx-template",
                    str(nginx_template),
                    "--output-root",
                    str(output_root),
                    "--compose-output-dir",
                    str(compose_output),
                    "--value",
                    "DATABASE_NAME=maxkey",
                    "--value",
                    "MAXKEY_ADMIN_BCRYPT=$2a$10$abc",
                    "--value",
                    "PUBLIC_BASE_URL=https://login.example.test",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output_root / "mysql" / "init" / "00-maxkey.sql").read_text(encoding="utf-8"), "database=maxkey\n")
            self.assertEqual((output_root / "mysql" / "init" / "01-hardening.sql").read_text(encoding="utf-8"), "hash=$2a$10$abc\n")
            self.assertEqual((compose_output / ".env").read_text(encoding="utf-8"), "PUBLIC_BASE_URL=https://login.example.test\n")
            self.assertEqual(
                (compose_output / "nginx" / "conf.d" / "maxkey.conf").read_text(encoding="utf-8"),
                "proxy_pass https://login.example.test;\n",
            )
