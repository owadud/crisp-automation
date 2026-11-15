"""Table models for large lists (thousands of rows stay fast: no widget per cell)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt


class RowsModel(QAbstractTableModel):
    """Plain rows of strings. append() adds rows without rebuilding the table."""

    TONE = Qt.UserRole + 1        # same role ChipDelegate reads

    def __init__(self, columns, parent=None, tone_of=None):
        super().__init__(parent)
        self.columns = tuple(columns)
        self.rows: list[tuple] = []
        self.tone_of = tone_of    # optional: row -> chip tone

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            row = self.rows[index.row()]
            return str(row[index.column()]) if index.column() < len(row) else ""
        if role == self.TONE and self.tone_of:
            return self.tone_of(self.rows[index.row()])
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section]
        return None

    def set_rows(self, rows) -> None:
        self.beginResetModel()
        self.rows = [tuple(r) for r in rows]
        self.endResetModel()

    def set_row(self, i: int, row) -> None:
        self.rows[i] = tuple(row)
        self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.columns) - 1))

    def pop(self) -> None:
        i = len(self.rows) - 1
        self.beginRemoveRows(QModelIndex(), i, i)
        self.rows.pop()
        self.endRemoveRows()

    def append(self, rows) -> None:
        rows = [tuple(r) for r in rows]
        if not rows:
            return
        start = len(self.rows)
        self.beginInsertRows(QModelIndex(), start, start + len(rows) - 1)
        self.rows.extend(rows)
        self.endInsertRows()
