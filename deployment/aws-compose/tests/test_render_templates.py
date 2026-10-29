import pathlib
import re
import os
import stat
import subprocess
import sys
import tempfile
import unittest

from deployment.aws_compose_renderer import render_bytes, render_text


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
COMPOSE_DIR = REPO_ROOT / "deployment" / "aws-compose"
SOURCE_SCHEMA = REPO_ROOT / "sql" / "v4.2.0" / "maxkey.sql"
HARDENING_TEMPLATE = COMPOSE_DIR / "mysql" / "init" / "01-hardening.sql.template"
RENDER_SCRIPT = COMPOSE_DIR / "scripts" / "render_templates.py"
MANAGEMENT_APP_ID = "622076759805923328"
VALID_BCRYPT = "$2b$12$" + ("A" * 53)
VALID_PUBLIC_BASE_URL = "https://login.example.test"


def normalize_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def rendered_hardening_sql() -> str:
    return render_text(
        HARDENING_TEMPLATE.read_text(encoding="utf-8"),
        {
            "MAXKEY_ADMIN_BCRYPT": VALID_BCRYPT,
            "PUBLIC_BASE_URL": VALID_PUBLIC_BASE_URL,
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
        self.assertEqual(render_text("__MAXKEY_ADMIN_BCRYPT__", {"MAXKEY_ADMIN_BCRYPT": VALID_BCRYPT}), VALID_BCRYPT)


class RenderBytesTest(unittest.TestCase):
    def test_real_seed_sql_without_placeholders_is_copied_byte_for_byte(self):
        seed = SOURCE_SCHEMA.read_bytes()

        self.assertEqual(render_bytes(seed, {}), seed)

    def test_ascii_placeholders_are_replaced_without_touching_other_bytes(self):
        template = b"\xff\xfe__PUBLIC_BASE_URL__\r\n\x00"

        rendered = render_bytes(template, {"PUBLIC_BASE_URL": VALID_PUBLIC_BASE_URL})

        self.assertEqual(rendered, b"\xff\xfehttps://login.example.test\r\n\x00")


class HardeningSqlTemplateTest(unittest.TestCase):
    def test_rendered_sql_contains_bcrypt_prefix_and_no_placeholders(self):
        sql = rendered_hardening_sql()

        self.assertIn(f"'{{bcrypt}}{VALID_BCRYPT}", sql)
        self.assertIsNone(re.search(r"__[A-Z0-9_]+__", sql))

    def test_admin_password_and_login_state_are_hardened(self):
        sql = normalize_sql(rendered_hardening_sql())

        self.assertIn(
            "UPDATE mxk_userinfo SET PASSWORD = "
            f"'{{bcrypt}}{VALID_BCRYPT}', "
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
            f"UPDATE mxk_apps SET LOGINURL = '{VALID_PUBLIC_BASE_URL}/maxkey-mgt' "
            f"WHERE ID = '{MANAGEMENT_APP_ID}';",
            sql,
        )
        self.assertIn(
            f"UPDATE mxk_apps_cas_details SET SERVICE = "
            f"'{VALID_PUBLIC_BASE_URL}/maxkey-mgt/passport/trust/auth', CALLBACKURL = "
            f"'{VALID_PUBLIC_BASE_URL}/maxkey-mgt/passport/trust/auth' "
            f"WHERE ID = '{MANAGEMENT_APP_ID}';",
            sql,
        )

    def test_marker_table_ddl_is_before_transaction_and_marker_insert_is_atomic(self):
        sql = rendered_hardening_sql()
        table = sql.index("CREATE TABLE IF NOT EXISTS maxkey_deployment_state")
        start = sql.index("START TRANSACTION")
        marker = sql.index("VALUES ('initial_hardening', 'complete')")
        commit = sql.index("COMMIT")

        self.assertLess(table, start)
        for statement in (
            "UPDATE mxk_userinfo\nSET PASSWORD",
            "UPDATE mxk_userinfo SET STATUS = 2",
            "UPDATE mxk_apps\nSET STATUS",
            "UPDATE mxk_apps\nSET LOGINURL",
            "UPDATE mxk_apps_cas_details\nSET SERVICE",
        ):
            self.assertLess(sql.index(statement), marker)
            self.assertGreater(sql.index(statement), start)
        self.assertLess(marker, commit)

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
    def write_templates(self, root: pathlib.Path) -> dict[str, pathlib.Path]:
        template_dir = root / "templates"
        template_dir.mkdir()
        paths = {
            "source_sql": template_dir / "maxkey.sql.template",
            "hardening_template": template_dir / "01-hardening.sql.template",
            "env_template": template_dir / ".env.template",
            "nginx_template": template_dir / "maxkey.conf.template",
        }
        paths["source_sql"].write_bytes(b"\xffseed\r\n")
        paths["hardening_template"].write_text("hash=__MAXKEY_ADMIN_BCRYPT__\nurl=__PUBLIC_BASE_URL__\n", encoding="utf-8")
        paths["env_template"].write_text("PUBLIC_BASE_URL=__PUBLIC_BASE_URL__\n", encoding="utf-8")
        paths["nginx_template"].write_text("proxy_pass __PUBLIC_BASE_URL__;\n", encoding="utf-8")
        return paths

    def render_command(self, root: pathlib.Path, *extra: str) -> list[str]:
        paths = self.write_templates(root)
        return [
            sys.executable,
            str(RENDER_SCRIPT),
            "--source-sql",
            str(paths["source_sql"]),
            "--hardening-template",
            str(paths["hardening_template"]),
            "--env-template",
            str(paths["env_template"]),
            "--nginx-template",
            str(paths["nginx_template"]),
            "--output-root",
            str(root / "srv" / "maxkey"),
            *extra,
        ]

    def required_values(self) -> list[str]:
        return [
            "--value",
            f"MAXKEY_ADMIN_BCRYPT={VALID_BCRYPT}",
            "--value",
            f"PUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}",
        ]

    def test_cli_renders_requested_files_without_reimplementing_placeholder_logic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            output_root = root / "srv" / "maxkey"

            result = subprocess.run(
                self.render_command(root, *self.required_values()),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output_root / "mysql" / "init" / "00-maxkey.sql").read_bytes(), b"\xffseed\r\n")
            self.assertEqual(
                (output_root / "mysql" / "init" / "01-hardening.sql").read_text(encoding="utf-8"),
                f"hash={VALID_BCRYPT}\nurl={VALID_PUBLIC_BASE_URL}\n",
            )
            self.assertEqual((output_root / ".env").read_text(encoding="utf-8"), f"PUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}\n")
            self.assertEqual(
                (output_root / "nginx" / "conf.d" / "maxkey.conf").read_text(encoding="utf-8"),
                f"proxy_pass {VALID_PUBLIC_BASE_URL};\n",
            )

    def test_values_file_can_supply_explicit_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            values_file = root / "values.env"
            values_file.write_text(
                f"MAXKEY_ADMIN_BCRYPT={VALID_BCRYPT}\nPUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}\n",
                encoding="utf-8-sig",
            )
            if os.name != "nt":
                values_file.chmod(0o600)

            result = subprocess.run(
                self.render_command(root, "--values-file", str(values_file)),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "srv" / "maxkey" / ".env").exists())

    def test_environment_variables_do_not_satisfy_placeholders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            env = os.environ.copy()
            env["MAXKEY_ADMIN_BCRYPT"] = VALID_BCRYPT
            env["PUBLIC_BASE_URL"] = VALID_PUBLIC_BASE_URL

            result = subprocess.run(
                self.render_command(root),
                check=False,
                capture_output=True,
                text=True,
                env=env,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(VALID_BCRYPT, result.stderr)
            self.assertFalse((root / "srv" / "maxkey" / ".env").exists())

    def test_failure_creates_no_partial_outputs_and_preserves_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            output_root = root / "srv" / "maxkey"
            existing = output_root / ".env"
            existing.parent.mkdir(parents=True)
            existing.write_text("old\n", encoding="utf-8")

            result = subprocess.run(
                self.render_command(root, "--value", f"PUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}"),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(existing.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((output_root / "mysql" / "init" / "00-maxkey.sql").exists())
            self.assertFalse((output_root / "mysql" / "init" / "01-hardening.sql").exists())
            self.assertFalse((output_root / "nginx" / "conf.d" / "maxkey.conf").exists())

    @unittest.skipIf(os.name == "nt", "Windows does not expose POSIX file modes reliably")
    def test_rendered_files_are_mode_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            result = subprocess.run(
                self.render_command(root, *self.required_values()),
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            for path in (
                root / "srv" / "maxkey" / "mysql" / "init" / "00-maxkey.sql",
                root / "srv" / "maxkey" / "mysql" / "init" / "01-hardening.sql",
                root / "srv" / "maxkey" / ".env",
                root / "srv" / "maxkey" / "nginx" / "conf.d" / "maxkey.conf",
            ):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)

    def test_cli_rejects_unknown_and_invalid_values_without_exposing_secrets(self):
        cases = (
            ("unknown", ["--value", f"MAXKEY_ADMIN_BCRYPT={VALID_BCRYPT}", "--value", f"PUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}", "--value", "UNUSED=secret-value"], "UNUSED", "secret-value"),
            ("empty", ["--value", "MAXKEY_ADMIN_BCRYPT=", "--value", f"PUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}"], "MAXKEY_ADMIN_BCRYPT", VALID_BCRYPT),
            ("bcrypt", ["--value", "MAXKEY_ADMIN_BCRYPT=$2b$12$short", "--value", f"PUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}"], "MAXKEY_ADMIN_BCRYPT", "$2b$12$short"),
            ("public_url", ["--value", f"MAXKEY_ADMIN_BCRYPT={VALID_BCRYPT}", "--value", "PUBLIC_BASE_URL=https://login.example.test/"], "PUBLIC_BASE_URL", "https://login.example.test/"),
            ("sql_quote", ["--value", f"MAXKEY_ADMIN_BCRYPT={VALID_BCRYPT}'", "--value", f"PUBLIC_BASE_URL={VALID_PUBLIC_BASE_URL}"], "MAXKEY_ADMIN_BCRYPT", f"{VALID_BCRYPT}'"),
        )
        for name, args, expected_key, leaked_value in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = pathlib.Path(tmp)
                result = subprocess.run(
                    self.render_command(root, *args),
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected_key, result.stderr)
                self.assertNotIn(leaked_value, result.stderr)


class StrictEnvExampleTest(unittest.TestCase):
    def test_env_example_uses_strict_placeholders_for_deployment_values(self):
        text = (COMPOSE_DIR / ".env.example").read_text(encoding="utf-8")

        self.assertNotIn("__PLACEHOLDER__", text)
        for forbidden in ("192.0.2.10", "generated-at-install", "age1example", "000000000000", "4.2.0-company.1-d7c9d9af"):
            self.assertNotIn(forbidden, text)
        for placeholder in (
            "__AWS_REGION__",
            "__ECR_REGISTRY__",
            "__IMAGE_VERSION__",
            "__PUBLIC_BASE_URL__",
            "__DATABASE_PWD__",
            "__MYSQL_ROOT_PASSWORD__",
            "__BACKUP_AGE_RECIPIENT__",
        ):
            self.assertIn(placeholder, text)

    def test_env_example_uses_admin_password_file_contract(self):
        text = (COMPOSE_DIR / ".env.example").read_text(encoding="utf-8")

        self.assertIn("ADMIN_INITIAL_PASSWORD_FILE=", text)
        self.assertNotIn("ADMIN_INITIAL_PASSWORD=", text)
