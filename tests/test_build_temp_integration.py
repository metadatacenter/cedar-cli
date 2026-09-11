"""Opt-in real npm/Surefire/Redis checks; CI supplies a noexec temporary root."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from org.metadatacenter.util.BuildSafety import (
    BuildSafetyError, executable_build_workspace, isolated_frontend_workspace,
)
from org.metadatacenter.util.InvocationContext import InvocationContext, use_context


@unittest.skipUnless(os.environ.get('CEDAR_BUILD_TEMP_INTEGRATION'), 'requires npm, Maven, Java 17')
class BuildTempIntegrationTest(unittest.TestCase):
    def test_frontend_binary_and_forked_redis_use_build_storage(self):
        # In Linux CI, this source directory is on a real noexec mount. The
        # configured build root is elsewhere, including a space to test quoting.
        with tempfile.TemporaryDirectory() as source_root, \
                tempfile.TemporaryDirectory(dir=Path.cwd(), prefix='build temp ') as home:
            if os.environ.get('CEDAR_TEST_NOEXEC'):
                probe = Path(source_root) / 'probe'
                probe.write_text('#!/bin/sh\nexit 0\n')
                probe.chmod(0o755)
                with self.assertRaises(PermissionError):
                    subprocess.run([str(probe)], check=True)
                with self.assertRaisesRegex(BuildSafetyError, 'does not permit execution'):
                    with executable_build_workspace({'CEDAR_BUILD_TMPDIR': source_root}):
                        self.fail('noexec workspace was accepted')
            source = Path(source_root) / 'frontend'
            source.mkdir()
            tool = source / 'tool'
            tool.mkdir()
            (tool / 'package.json').write_text(json.dumps({
                'name': 'fixture-builder', 'version': '1.0.0', 'bin': {'fixture-builder': 'build.js'}}))
            (tool / 'build.js').write_text(
                '#!/usr/bin/env node\nrequire("fs").writeFileSync("built.txt", "built");\n')
            (tool / 'build.js').chmod(0o755)
            (source / 'package.json').write_text(json.dumps({
                'name': 'fixture', 'version': '1.0.0', 'scripts': {'build': 'fixture-builder'},
                'devDependencies': {'fixture-builder': 'file:./tool'}}))
            subprocess.run(['npm', 'install', '--package-lock-only', '--ignore-scripts',
                            '--no-audit', '--no-fund'], cwd=source, check=True, capture_output=True)
            env = {**os.environ, 'CEDAR_HOME': home}
            env['JAVA_TOOL_OPTIONS'] = f'"-Djava.io.tmpdir={source_root}"'
            env.pop('CEDAR_BUILD_TMPDIR', None)
            with use_context(InvocationContext(environment=env)):
                with isolated_frontend_workspace(source) as (build, child_env, _):
                    for command in (['npm', 'ci', '--no-audit', '--no-fund'], ['npm', 'run', 'build']):
                        result = subprocess.run(command, cwd=build, env=child_env,
                                                text=True, capture_output=True)
                        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertTrue((build / 'built.txt').exists())
                self.assertFalse(build.exists())

            project = Path(home) / 'java'
            test = project / 'src/test/java/TempTest.java'
            test.parent.mkdir(parents=True)
            (project / 'pom.xml').write_text('''<project>
<modelVersion>4.0.0</modelVersion><groupId>test</groupId><artifactId>temp</artifactId><version>1</version>
<properties><maven.compiler.source>17</maven.compiler.source><maven.compiler.target>17</maven.compiler.target></properties>
<dependencies>
<dependency><groupId>com.github.codemonstur</groupId><artifactId>embedded-redis</artifactId><version>1.4.4</version><scope>test</scope></dependency>
<dependency><groupId>junit</groupId><artifactId>junit</artifactId><version>4.13.2</version><scope>test</scope></dependency>
</dependencies><build><plugins><plugin><groupId>org.apache.maven.plugins</groupId>
<artifactId>maven-surefire-plugin</artifactId><version>3.5.2</version>
<configuration><forkCount>1</forkCount><reuseForks>false</reuseForks></configuration>
</plugin></plugins></build></project>''')
            test.write_text('''import org.junit.Test;
import static org.junit.Assert.*;
import java.net.ServerSocket;
import java.nio.file.*;
import redis.embedded.RedisServer;
public class TempTest {
 @Test public void executableRedisInForkedJvm() throws Exception {
   assertEquals(System.getenv("TMPDIR"), System.getProperty("java.io.tmpdir"));
   Path file = Files.createTempFile("cedar", ".tmp");
   assertTrue(file.startsWith(Path.of(System.getenv("TMPDIR"))));
   int port;
   try (ServerSocket socket = new ServerSocket(0)) { port = socket.getLocalPort(); }
   RedisServer redis = new RedisServer(port);
   try { redis.start(); assertTrue(redis.isActive()); } finally { redis.stop(); }
 }
}''')
            with executable_build_workspace(env, java=True) as (build, child_env):
                result = subprocess.run([shutil.which('mvn'), '-B', '-ntp', 'test'],
                                        cwd=project, env=child_env, text=True, capture_output=True)
                self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertFalse(build.exists())
