import unittest

from .load_fixtures import LoadFixture


class TestLoadFixture(unittest.TestCase):

    def test_get_table(self) -> None:
        lf = LoadFixture("01_page_page.json")
        assert lf.table == "page_page"

        lf = LoadFixture("page_page.json")
        assert lf.table == "page_page"

        lf = LoadFixture("01_page_page.das.json")
        assert lf.table == "page_page.das"

        lf = LoadFixture("01_pagepage.json")
        assert lf.table == "pagepage"

        lf = LoadFixture("pagepage")
        assert lf.table == "pagepage"
