"""Compatibility settings access; each invocation owns its mutable settings."""
from org.metadatacenter.util.InvocationContext import ContextAttribute, current_context


class CedarCliSettings:
    do_fail_on_error = ContextAttribute('settings.do_fail_on_error')
    skip_tests = ContextAttribute('settings.skip_tests')
    shell_path = ContextAttribute('settings.shell_path')

    @classmethod
    def get_sed_replace_in_place(cls):
        return current_context().settings.get_sed_replace_in_place()
