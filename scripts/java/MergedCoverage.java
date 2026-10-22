import org.jacoco.core.analysis.Analyzer;
import org.jacoco.core.analysis.CoverageBuilder;
import org.jacoco.core.analysis.IBundleCoverage;
import org.jacoco.core.analysis.ICounter;
import org.jacoco.core.data.ExecutionDataStore;
import org.jacoco.core.tools.ExecFileLoader;

import java.io.File;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

/**
 * Line and branch coverage of each module's classes, credited with every test JVM of a build, and
 * with the test JVMs of the module's own repository.
 *
 * <p>A module's JaCoCo report credits its classes only with its own tests, so a shared library's
 * code that a server's suites run counts in neither report. This program merges the execution data
 * of every test JVM and analyses each module's classes against it. It also analyses them against the
 * execution data of the module's repository alone: that is what the repository's own CI runs, and a
 * module tested from a sibling module, such as a library tested through its repository's test
 * fixtures, is covered by that CI though not by its own tests.
 *
 * <p>Arguments: a file listing execution-data files, one per line as the repository and the file
 * separated by a tab, and a file with one line per module: the repository, the module directory,
 * then its class files, separated by tabs. The class files are those the module's own report
 * analysed, so the exclusions its POM configures carry over. Prints one line per module: the
 * directory, covered and total lines, covered and total branches, then lines and branches covered by
 * the repository's tests, separated by tabs. Each module gets its own analysis, because two modules
 * may compile a class of the same name.
 *
 * <p>Run as a single-file source program, with JaCoCo's core library and ASM on the class path.
 */
public class MergedCoverage {

  public static void main(String[] args) throws Exception {
    ExecFileLoader executions = new ExecFileLoader();
    Map<String, ExecFileLoader> byRepository = new HashMap<>();
    for (String line : Files.readAllLines(Path.of(args[0]))) {
      if (line.isBlank()) continue;
      String[] fields = line.split("\t", 2);
      File execution = new File(fields[1]);
      executions.load(execution);
      byRepository.computeIfAbsent(fields[0], repository -> new ExecFileLoader()).load(execution);
    }
    for (String line : Files.readAllLines(Path.of(args[1]))) {
      if (line.isBlank()) continue;
      String[] fields = line.split("\t");
      IBundleCoverage any = analyse(executions.getExecutionDataStore(), fields);
      ExecFileLoader repositoryExecutions = byRepository.get(fields[0]);
      IBundleCoverage repository = analyse(repositoryExecutions == null ? new ExecutionDataStore()
          : repositoryExecutions.getExecutionDataStore(), fields);
      ICounter lines = any.getLineCounter();
      ICounter branches = any.getBranchCounter();
      System.out.printf("%s\t%d\t%d\t%d\t%d\t%d\t%d%n", fields[1], lines.getCoveredCount(), lines.getTotalCount(),
          branches.getCoveredCount(), branches.getTotalCount(),
          repository.getLineCounter().getCoveredCount(), repository.getBranchCounter().getCoveredCount());
    }
  }

  private static IBundleCoverage analyse(ExecutionDataStore store, String[] fields) throws Exception {
    CoverageBuilder builder = new CoverageBuilder();
    Analyzer analyzer = new Analyzer(store, builder);
    for (int i = 2; i < fields.length; i++) {
      Path classFile = Path.of(fields[i]);
      if (!Files.isRegularFile(classFile)) continue;
      try (InputStream in = Files.newInputStream(classFile)) {
        analyzer.analyzeClass(in, classFile.toString());
      }
    }
    // The bundle counts each source file's lines once, as the module's report does. Summing the
    // classes would count twice a line that an outer class and a class declared on it share.
    return builder.getBundle(fields[1]);
  }
}
