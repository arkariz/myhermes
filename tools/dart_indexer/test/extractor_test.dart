import 'dart:io';

import 'package:dart_indexer/src/extractor.dart';
import 'package:dart_indexer/src/graph.dart';
import 'package:test/test.dart';

/// A real temp directory per test, real file I/O, real analyzer parsing --
/// no mocking. The property under test is "does this AST shape produce
/// this graph shape," which a fake parser couldn't stand in for.
Directory _writeProject(Map<String, String> files) {
  final dir = Directory.systemTemp.createTempSync('dart_indexer_test_');
  for (final entry in files.entries) {
    final file = File('${dir.path}/${entry.key}');
    file.parent.createSync(recursive: true);
    file.writeAsStringSync(entry.value);
  }
  return dir;
}

Node? findNode(Graph graph, String id) {
  for (final n in graph.nodes) {
    if (n.id == id) return n;
  }
  return null;
}

bool hasEdge(Graph graph, String src, String dst, String relation) {
  return graph.edges.any((e) => e.src == src && e.dst == dst && e.relation == relation);
}

void main() {
  test('a widget class is detected by its superclass name', () {
    final dir = _writeProject({
      'lib/app.dart': '''
class MyApp extends StatelessWidget {
  const MyApp();
}
''',
    });

    final graph = Extractor(dir).run();

    final node = findNode(graph, 'lib/app.dart#MyApp');
    expect(node, isNotNull);
    expect(node!.kind, 'widget');
    expect(node.line, 1);
  });

  test('a plain class is not misclassified as a widget', () {
    final dir = _writeProject({'lib/habit.dart': 'class Habit {}'});
    final graph = Extractor(dir).run();

    expect(findNode(graph, 'lib/habit.dart#Habit')!.kind, 'class');
  });

  test('import directives become imports edges', () {
    final dir = _writeProject({
      'lib/main.dart': "import 'habit.dart';\nvoid main() {}\n",
    });
    final graph = Extractor(dir).run();

    expect(hasEdge(graph, 'lib/main.dart', 'habit.dart', 'imports'), isTrue);
  });

  test('methods are declared under their class, not the file', () {
    final dir = _writeProject({
      'lib/counter.dart': '''
class Counter {
  int value = 0;
  void increment() { value++; }
}
''',
    });
    final graph = Extractor(dir).run();

    expect(findNode(graph, 'lib/counter.dart#Counter.increment')?.kind, 'method');
    expect(hasEdge(graph, 'lib/counter.dart#Counter', 'lib/counter.dart#Counter.increment', 'declares'), isTrue);
    // Not declared directly by the file -- the class owns it.
    expect(hasEdge(graph, 'lib/counter.dart', 'lib/counter.dart#Counter.increment', 'declares'), isFalse);
  });

  test('mixins, enums, extensions, and top-level functions are all captured', () {
    final dir = _writeProject({
      'lib/misc.dart': '''
mixin Loggable {}
enum Status { on, off }
extension StringExt on String {}
int square(int x) => x * x;
''',
    });
    final graph = Extractor(dir).run();

    expect(findNode(graph, 'lib/misc.dart#Loggable')?.kind, 'mixin');
    expect(findNode(graph, 'lib/misc.dart#Status')?.kind, 'enum');
    expect(findNode(graph, 'lib/misc.dart#StringExt')?.kind, 'extension');
    expect(findNode(graph, 'lib/misc.dart#square')?.kind, 'function');
  });

  test('extends/implements/with produce edges naming the type by identifier', () {
    final dir = _writeProject({
      'lib/thing.dart': '''
class Base {}
mixin M {}
abstract class Iface {}
class Thing extends Base with M implements Iface {}
''',
    });
    final graph = Extractor(dir).run();

    expect(hasEdge(graph, 'lib/thing.dart#Thing', 'Base', 'extends'), isTrue);
    expect(hasEdge(graph, 'lib/thing.dart#Thing', 'M', 'with'), isTrue);
    expect(hasEdge(graph, 'lib/thing.dart#Thing', 'Iface', 'implements'), isTrue);
  });

  test('a local function inside a method body is not indexed as a symbol', () {
    final dir = _writeProject({
      'lib/local.dart': '''
class Holder {
  void run() {
    int helper() => 1;
    helper();
  }
}
''',
    });
    final graph = Extractor(dir).run();

    expect(findNode(graph, 'lib/local.dart#helper'), isNull);
  });

  test('a file outside lib/ and test/ is not walked at all', () {
    final dir = _writeProject({'bin/main.dart': 'class Ignored {}'});
    final graph = Extractor(dir).run();

    expect(findNode(graph, 'bin/main.dart#Ignored'), isNull);
  });

  test('an unparseable file is skipped with a warning, not a crash', () {
    final dir = _writeProject({'lib/broken.dart': 'this is not valid dart {{{'});
    final extractor = Extractor(dir);

    expect(() => extractor.run(), returnsNormally);
  });

  test('output is deterministic across runs on the same project', () {
    final dir = _writeProject({
      'lib/b.dart': 'class B {}',
      'lib/a.dart': 'class A {}',
    });

    final first = Extractor(dir).run().toJsonString();
    final second = Extractor(dir).run().toJsonString();

    expect(first, second);
  });
}
