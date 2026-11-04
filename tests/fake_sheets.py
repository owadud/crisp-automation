"""In-memory stand-in for gspread (client -> spreadsheet -> worksheets)."""

import re


class FakeAPIError(Exception):
    def __init__(self, code, msg="error"):
        super().__init__(msg)
        self.response = type("R", (), {"status_code": code})()


def _parse(a1):
    m = re.fullmatch(r"([A-Z]+)(\d+)", a1)
    col = 0
    for ch in m.group(1):
        col = col * 26 + (ord(ch) - 64)
    return int(m.group(2)), col


class FakeWorksheet:
    def __init__(self, title, values):
        self.title = title
        self.values = [list(r) for r in values]
        self.hidden = False
        self.offline = False
        self.writes = 0

    def _check(self):
        if self.offline:
            raise ConnectionError("network down")

    def get_all_values(self):
        self._check()
        rows = [list(r) for r in self.values]
        while rows and not any(rows[-1]):
            rows.pop()
        return rows

    def col_values(self, col):
        self._check()
        return [r[col - 1] if len(r) >= col else "" for r in self.values]

    def _set(self, row, col, value):
        while len(self.values) < row:
            self.values.append([])
        r = self.values[row - 1]
        while len(r) < col:
            r.append("")
        r[col - 1] = value

    def update(self, values=None, range_name="A1", value_input_option="RAW"):
        self._check()
        self.writes += 1
        row, col = _parse(range_name)
        for i, line in enumerate(values):
            for j, v in enumerate(line):
                self._set(row + i, col + j, v)

    def batch_update(self, data, value_input_option="RAW"):
        self._check()
        self.writes += 1
        for item in data:
            row, col = _parse(item["range"])
            v = item["values"][0][0]
            if value_input_option == "USER_ENTERED" and v == "TRUE":
                v = "TRUE"   # a checkbox cell shows ticked
            self._set(row, col, v)

    def hide(self):
        self.hidden = True

    def cell(self, row, header_name):
        col = self.values[0].index(header_name)
        r = self.values[row - 1]
        return r[col] if col < len(r) else ""


class FakeSpreadsheet:
    def __init__(self, tabs):
        self.tabs = {t: FakeWorksheet(t, v) for t, v in tabs.items()}

    def worksheets(self):
        return list(self.tabs.values())

    def worksheet(self, title):
        if title not in self.tabs:
            raise FakeAPIError(400, f"WorksheetNotFound: {title}")
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title, [])
        return self.tabs[title]


class FakeClient:
    def __init__(self, spreadsheet, sheet_id):
        self.spreadsheet, self.sheet_id = spreadsheet, sheet_id

    def open_by_key(self, key):
        if key != self.sheet_id:
            raise FakeAPIError(404, "not found or not shared")
        return self.spreadsheet
