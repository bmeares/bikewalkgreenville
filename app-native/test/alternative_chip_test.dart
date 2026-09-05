import 'package:flutter/material.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:bwg_app_native/nav.dart';
import 'package:bwg_app_native/widgets/alternative_chip.dart';

void main() {
  final route = NavRoute.fromFeature({
    'type': 'Feature',
    'geometry': {'type': 'LineString', 'coordinates': [[-82.4, 34.85], [-82.398, 34.85]]},
    'properties': {
      'distance_m': 405.0, 'duration_min': 3.0, 'climb_ft': 100, 'steps': [],
      'alternatives': [
        {
          'plan': 'walk', 'label': 'Walk', 'icon_mode': 'walk',
          'distance_m': 405.0, 'duration_min': 7.0, 'climb_ft': 100,
          'warnings': [
            {'kind': 'no_sidewalk', 'distance_m': 600.0, 'label': '0.4 mi with no sidewalk', 'message': 'y'},
          ],
        },
      ],
    },
  });

  testWidgets('one semantic node with the label, a tap action, and no echo', (tester) async {
    final handle = tester.ensureSemantics();
    var taps = 0;
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        body: AlternativeChip(
          alt: route.alternatives.single,
          selected: route,
          ebike: false,
          onPressed: () => taps++,
        ),
      ),
    ));
    final node = tester.getSemantics(find.byType(AlternativeChip));
    expect(node.label, 'Walk, +4 min, 0.4 mi with no sidewalk');
    expect(node.flagsCollection.isButton, isTrue);
    expect(node.getSemanticsData().hasAction(SemanticsAction.tap), isTrue);
    // No child nodes re-announcing the visible text or tooltip.
    var children = 0;
    node.visitChildren((_) { children++; return true; });
    expect(children, 0);
    // The assistive tap drives the same callback as a touch.
    tester.binding.performSemanticsAction(
      SemanticsActionEvent(type: SemanticsAction.tap, viewId: tester.view.viewId, nodeId: node.id),
    );
    await tester.pump();
    await tester.tap(find.byType(ActionChip));
    expect(taps, 2);
    handle.dispose();
  });
}
