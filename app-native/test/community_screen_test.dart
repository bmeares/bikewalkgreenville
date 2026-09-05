import 'package:bwg_app_native/screens/community_screen.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('geometryLatLngs flattens point, line and polygon', () {
    expect(geometryLatLngs({'type': 'Point', 'coordinates': [-82.4, 34.85]}).single.latitude, 34.85);
    expect(geometryLatLngs({'type': 'LineString', 'coordinates': [[-82.4, 34.85], [-82.39, 34.86]]}).length, 2);
    expect(
      geometryLatLngs({'type': 'Polygon', 'coordinates': [[[-82.4, 34.85], [-82.39, 34.85], [-82.39, 34.86], [-82.4, 34.85]]]}).length,
      4,
    );
    expect(geometryLatLngs(null), isEmpty);
    expect(geometryLatLngs({'type': 'Point', 'coordinates': []}), isEmpty);
  });
}
