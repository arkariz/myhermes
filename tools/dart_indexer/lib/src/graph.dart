import 'dart:convert';

/// One symbol or file in the codebase graph (docs/plan.md's normalized
/// schema: `{id, kind, name, file, line, module, lang}`).
class Node {
  Node({
    required this.id,
    required this.kind,
    required this.name,
    required this.file,
    required this.line,
    this.module,
    this.lang = 'dart',
  });

  final String id;
  final String kind; // file | class | widget | mixin | enum | extension | function | method
  final String name;
  final String file;
  final int line;
  final String? module;
  final String lang;

  Map<String, dynamic> toJson() => {
        'id': id,
        'kind': kind,
        'name': name,
        'file': file,
        'line': line,
        'module': module,
        'lang': lang,
      };
}

/// One relationship between two nodes. `confidence` is 1.0 for edges the
/// AST states outright (imports, declares) and lower for edges naming a
/// type by its bare identifier without resolution behind it (extends,
/// implements, with) -- syntactically correct almost always, but not
/// proven the way a resolved analysis would be.
class Edge {
  Edge({
    required this.src,
    required this.dst,
    required this.relation,
    this.confidence = 1.0,
  });

  final String src;
  final String dst;
  final String relation; // imports | declares | extends | implements | with
  final double confidence;

  Map<String, dynamic> toJson() => {
        'src': src,
        'dst': dst,
        'relation': relation,
        'confidence': confidence,
      };
}

class Graph {
  final List<Node> nodes = [];
  final List<Edge> edges = [];

  void addNode(Node n) => nodes.add(n);
  void addEdge(Edge e) => edges.add(e);

  Map<String, dynamic> toJson() => {
        'nodes': nodes.map((n) => n.toJson()).toList(),
        'edges': edges.map((e) => e.toJson()).toList(),
      };

  String toJsonString() => const JsonEncoder.withIndent('  ').convert(toJson());
}
