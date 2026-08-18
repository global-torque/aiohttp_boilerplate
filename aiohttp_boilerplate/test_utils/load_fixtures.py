import datetime
import json
import re
from pathlib import Path
from typing import Any

from aiohttp_boilerplate.sql import validate_identifier


class LoadFixture:

    def __init__(self, file_name: str, directory: str = "./", table: str | None = None) -> None:
        self.table = table
        self.directory = directory

        if len(file_name) == 0:
            raise Exception("You have to set up a json file")

        self.data: list[dict[str, Any]] = []
        self.file = file_name

        if self.table is None:
            self.get_table()

    def remove_ext(self, t: str | None = None) -> str:

        if t is None:
            t = self.file

        try:
            return t.rsplit(".", 1)[0]
        except ValueError:
            return t

    def get_table(self) -> None:
        """
        Tranfrom 01_<table_name>.json to table_name
        remove everything after first .
        remove before first _
        """

        if self.table is None:
            t = self.file
            try:
                t = self.remove_ext(t)
                idx = t.index("_")
                int(t[0:idx])
                t = t[idx + 1 :]
            except ValueError:
                pass

            self.table = t

    async def truncate(self, con: Any) -> None:
        if self.table is None:
            raise RuntimeError("fixture table was not resolved")
        table = validate_identifier(self.table, kind="fixture table")
        await con.execute(f"TRUNCATE {table} RESTART IDENTITY CASCADE")

    async def file2db(self, con: Any) -> None:
        """Read data from file and save in the data array"""
        filename = Path(self.directory) / self.file
        if self.table is None:
            raise RuntimeError("fixture table was not resolved")
        table = validate_identifier(self.table, kind="fixture table")
        loaded = json.loads(filename.read_text(encoding="utf-8"))
        if not isinstance(loaded, list):
            raise ValueError("fixture file must contain a JSON array")
        self.data = [dict(row) for row in loaded]

        await con.execute(f"DELETE FROM {table}")

        for source_row in self.data:
            row = dict(source_row)
            field_names: list[str] = []
            field_placeholders: list[str] = []

            i = 1
            for f in row:
                field = validate_identifier(f, kind="fixture column")
                field_names.append(f'"{field}"')
                field_placeholders.append(f"${i}")
                i += 1
            # FIXME
            sql = "INSERT INTO {} ({}) VALUES ({})".format(  # nosec
                table, ",".join(field_names), ",".join(field_placeholders)
            )
            stmt = await con.prepare(sql)

            # Convert ISO timestamp strings such as 2022-12-29T20:36:42.611271+00:00.
            number_pattern = "\\d{4}-\\d{2}-\\d{2}T\\d{2}.*"
            for key in row:
                val = row[key]
                if isinstance(val, str) and re.match(number_pattern, val) is not None:
                    row[key] = datetime.datetime.fromisoformat(val)
            await stmt.fetch(*row.values())

        # Empty tables must yield 1 next; populated tables must yield max(id)+1.
        await con.execute(
            f"SELECT setval(pg_get_serial_sequence($1, 'id'), "
            f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}",
            table,
        )
