import org.jacoco.core.analysis.Analyzer;
import org.jacoco.core.analysis.CoverageBuilder;
import org.jacoco.core.analysis.IBundleCoverage;
import org.jacoco.core.analysis.ICounter;
import org.jacoco.core.tools.ExecFileLoader;

import java.io.File;
import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Line and branch coverage of each module's classes, credited with every test JVM of a build.
 *
 * <p>A module's JaCoCo report credits its classes only with its own tests, so a shared library's
 * code that a server's suites run counts in neither report. This program merges the execution data
 * of every test JVM and analyses each module's classes against it.
 *
 * <p>Arguments: a file listing execution-data files, one per line, and a file with one line per
 * module: the module directory, then its class files, separated by tabs. The class files are those
 * the module's own report analysed, so the exclusions its POM configures carry over. Prints one
 * line per module: the directory, then covered and total lines, then covered and total branches,
 * separated by tabs. Each module gets its own analysis, because two modules may compile a class of
 * the same name.
 *
 * <p>Run as a single-file source program, with JaCoCo's core library and ASM on the class path.
 */
public class MergedCoverage {

  public static void main(String[] args) throws Exception {
    ExecFileLoader executions = new ExecFileLoader();
    for (String execution : Files.readAllLines(Path.of(args[0]))) {
      if (!execution.isBlank()) executions.load(new File(execution));
    }
    for (String line : Files.readAllLines(Path.of(args[1]))) {
      if (line.isBlank()) continue;
      String[] fields = line.split("\t");
      CoverageBuilder builder = new CoverageBuilder();
      Analyzer analyzer = new Analyzer(executions.getExecutionDataStore(), builder);
      for (int i = 1; i < fields.length; i++) {
        Path classFile = Path.of(fields[i]);
        if (!Files.isRegularFile(classFile)) continue;
        try (InputStream in = Files.newInputStream(classFile)) {
          analyzer.analyzeClass(in, classFile.toString());
        }
      }
      // The bundle counts each source file's lines once, as the module's report does. Summing the
      // classes would count twice a line that an outer class and a class declared on it share.
      IBundleCoverage bundle = builder.getBundle(fields[0]);
      ICounter lines = bundle.getLineCounter();
      ICounter branches = bundle.getBranchCounter();
      System.out.printf("%s\t%d\t%d\t%d\t%d%n", fields[0], lines.getCoveredCount(), lines.getTotalCount(),
          branches.getCoveredCount(), branches.getTotalCount());
    }
  }
}
