class Repo:
    def __init__(self, name, repo_type, is_client=False, is_library=False, is_microservice=False, is_private=False, for_docker=False,
                 dist_src="", expected_build_lines=100):
        self.name = name
        self.repo_type = repo_type
        self.is_client = is_client
        self.is_library = is_library
        self.is_microservice = is_microservice
        self.is_private = is_private
        self.for_docker = for_docker
        self.dist_src = dist_src
        self.expected_build_lines = expected_build_lines
