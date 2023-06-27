from org.metadatacenter.model.DomainMode import DomainMode


class Subdomain:

    def __init__(self, name: str, domain_mode: DomainMode) -> None:
        self.name = name
        self.domain_mode = domain_mode

    def get_fqdn(self):
        from org.metadatacenter.util.GlobalContext import GlobalContext
        if self.name == '':
            return GlobalContext.get_ca_common_name()
        else:
            return self.name + '.' + GlobalContext.get_ca_common_name()

    def get_config_file_name(self):
        subdomain_file_name = 'openssl-'
        if self.name == '':
            subdomain_file_name += '-nosubdomain'
        else:
            subdomain_file_name += self.name
        return subdomain_file_name + '.cnf'

    def get_cert_directory_name(self):
        from org.metadatacenter.util.GlobalContext import GlobalContext
        subdomain_directory = GlobalContext.get_ca_common_name()
        if self.name == '':
            subdomain_directory = '-' + subdomain_directory
        else:
            subdomain_directory = self.name + '.' + subdomain_directory
        return subdomain_directory
