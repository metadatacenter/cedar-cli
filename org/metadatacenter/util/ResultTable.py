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
        self.results = []

    def add_result(self, triple):
        self.table.add_row(triple.repo.name, triple.out, triple.err)
        self.results.append(triple)

    def print_table(self):
        console.print(self.table)
