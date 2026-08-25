import 'dart:convert';
import 'dart:io';

import 'package:dart_indexer/src/extractor.dart';

/// Usage: dart run bin/dart_indexer.dart <project_root>
///
/// Prints the graph as JSON on stdout; warnings (unparseable files) go to
/// stderr so a caller capturing stdout gets clean JSON either way.
void main(List<String> args) {
  if (args.isEmpty) {
    stderr.writeln('Usage: dart run bin/dart_indexer.dart <project_root>');
    exitCode = 2;
    return;
  }

  final projectRoot = Directory(args[0]);
  if (!projectRoot.existsSync()) {
    stderr.writeln('No such directory: ${args[0]}');
    exitCode = 2;
    return;
  }

  final extractor = Extractor(projectRoot);
  final graph = extractor.run();

  for (final warning in extractor.warnings) {
    stderr.writeln('warning: $warning');
  }

  stdout.writeln(const JsonEncoder.withIndent('  ').convert(graph.toJson()));
}
