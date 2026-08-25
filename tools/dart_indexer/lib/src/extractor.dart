import 'dart:io';

import 'package:analyzer/dart/analysis/results.dart';
import 'package:analyzer/dart/analysis/utilities.dart';
import 'package:analyzer/dart/ast/ast.dart';
import 'package:analyzer/dart/ast/visitor.dart';
import 'package:analyzer/source/line_info.dart';
import 'package:path/path.dart' as p;

import 'graph.dart';

/// Widget detection is a name check against Flutter's own base classes,
/// not a resolved type check -- this indexer never loads Flutter's SDK
/// sources, on purpose (that's what makes it fast enough to run without a
/// prior `flutter pub get`). A user class that happens to be named
/// `StatelessWidget` unrelated to Flutter would be a false positive; in
/// practice this doesn't happen.
const _widgetBaseNames = {'StatelessWidget', 'StatefulWidget', 'State'};

/// Walks `lib/` and `test/` under a project root, parsing each `.dart`
/// file with `package:analyzer`'s AST-only parser (no resolved type
/// analysis, no `.dart_tool/package_config.json` required) and emitting a
/// `Graph` of files, declarations, and the structural edges between them.
class Extractor {
  Extractor(this.projectRoot);

  final Directory projectRoot;
  final Graph graph = Graph();
  final List<String> warnings = [];

  Graph run() {
    for (final dirName in ['lib', 'test']) {
      final dir = Directory(p.join(projectRoot.path, dirName));
      if (!dir.existsSync()) continue;

      final dartFiles = dir
          .listSync(recursive: true)
          .whereType<File>()
          .where((f) => f.path.endsWith('.dart'))
          .toList()
        ..sort((a, b) => a.path.compareTo(b.path)); // deterministic output

      for (final file in dartFiles) {
        _processFile(file);
      }
    }
    return graph;
  }

  void _processFile(File file) {
    final relPath = p
        .relative(file.path, from: projectRoot.path)
        .replaceAll('\\', '/');

    final String content;
    try {
      content = file.readAsStringSync();
    } on FileSystemException catch (e) {
      warnings.add('could not read $relPath: $e');
      return;
    }

    late final ParseStringResult result;
    try {
      result = parseString(content: content, path: file.path, throwIfDiagnostics: false);
    } catch (e) {
      warnings.add('could not parse $relPath: $e');
      return;
    }

    graph.addNode(Node(id: relPath, kind: 'file', name: relPath, file: relPath, line: 0));

    result.unit.accept(_FileVisitor(graph, relPath, result.unit.lineInfo));
  }
}

class _FileVisitor extends RecursiveAstVisitor<void> {
  _FileVisitor(this.graph, this.file, this.lineInfo);

  final Graph graph;
  final String file;
  final LineInfo lineInfo;

  int _lineOf(AstNode node) => lineInfo.getLocation(node.offset).lineNumber;

  @override
  void visitImportDirective(ImportDirective node) {
    final uri = node.uri.stringValue;
    if (uri != null) {
      graph.addEdge(Edge(src: file, dst: uri, relation: 'imports'));
    }
    super.visitImportDirective(node);
  }

  @override
  void visitClassDeclaration(ClassDeclaration node) {
    final name = node.name.lexeme;
    final id = '$file#$name';
    final superclassName = node.extendsClause?.superclass.name2.lexeme;
    final kind = _widgetBaseNames.contains(superclassName) ? 'widget' : 'class';

    graph.addNode(Node(id: id, kind: kind, name: name, file: file, line: _lineOf(node)));
    graph.addEdge(Edge(src: file, dst: id, relation: 'declares'));

    if (superclassName != null) {
      graph.addEdge(Edge(src: id, dst: superclassName, relation: 'extends', confidence: 0.8));
    }
    for (final iface in node.implementsClause?.interfaces ?? const <NamedType>[]) {
      graph.addEdge(Edge(src: id, dst: iface.name2.lexeme, relation: 'implements', confidence: 0.8));
    }
    for (final mixin in node.withClause?.mixinTypes ?? const <NamedType>[]) {
      graph.addEdge(Edge(src: id, dst: mixin.name2.lexeme, relation: 'with', confidence: 0.8));
    }

    for (final member in node.members) {
      if (member is MethodDeclaration) {
        final methodId = '$id.${member.name.lexeme}';
        graph.addNode(Node(
          id: methodId, kind: 'method', name: member.name.lexeme,
          file: file, line: _lineOf(member),
        ));
        graph.addEdge(Edge(src: id, dst: methodId, relation: 'declares'));
      }
    }

    super.visitClassDeclaration(node);
  }

  @override
  void visitMixinDeclaration(MixinDeclaration node) {
    final name = node.name.lexeme;
    final id = '$file#$name';
    graph.addNode(Node(id: id, kind: 'mixin', name: name, file: file, line: _lineOf(node)));
    graph.addEdge(Edge(src: file, dst: id, relation: 'declares'));
    super.visitMixinDeclaration(node);
  }

  @override
  void visitEnumDeclaration(EnumDeclaration node) {
    final name = node.name.lexeme;
    final id = '$file#$name';
    graph.addNode(Node(id: id, kind: 'enum', name: name, file: file, line: _lineOf(node)));
    graph.addEdge(Edge(src: file, dst: id, relation: 'declares'));
    super.visitEnumDeclaration(node);
  }

  @override
  void visitExtensionDeclaration(ExtensionDeclaration node) {
    final name = node.name?.lexeme ?? '(unnamed)';
    final id = '$file#$name';
    graph.addNode(Node(id: id, kind: 'extension', name: name, file: file, line: _lineOf(node)));
    graph.addEdge(Edge(src: file, dst: id, relation: 'declares'));
    super.visitExtensionDeclaration(node);
  }

  @override
  void visitFunctionDeclaration(FunctionDeclaration node) {
    // Only top-level functions -- local functions inside a method body
    // aren't symbols another file could reference, so they add noise
    // without adding anything a context provider could use.
    if (node.parent is CompilationUnit) {
      final name = node.name.lexeme;
      final id = '$file#$name';
      graph.addNode(Node(id: id, kind: 'function', name: name, file: file, line: _lineOf(node)));
      graph.addEdge(Edge(src: file, dst: id, relation: 'declares'));
    }
    super.visitFunctionDeclaration(node);
  }
}
