import typer


def exit_on_failure(result):
    """Propagate a worker or subprocess exit code through a Typer command."""
    returncode = getattr(result, "returncode", result if isinstance(result, int) else 0)
    if isinstance(returncode, int) and returncode:
        raise typer.Exit(code=returncode)
    return result
