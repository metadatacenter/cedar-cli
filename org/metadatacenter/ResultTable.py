from rich.console import Console
from rich.table import Table

console = Console()


class ResultTable:
    def __init__(self, headers, show_lines):
        self.headers = headers
        self.show_lines = show_lines
        self.table = Table(show_lines=show_lines)
        for column_name in headers:
            self.table.add_column(column_name)
        self.lines = []

    def add_line(self, name, out, err):
        self.table.add_row(name, out, err)
        self.lines.append([name, out, err])

    def print_table(self):
        console.print(self.table)
