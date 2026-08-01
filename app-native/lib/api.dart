import 'dart:typed_data';

import 'package:dio/dio.dart';

class ApiError implements Exception {
  final String message;
  ApiError(this.message);
  @override
  String toString() => message;
}

/// Thin client for the BWG Meerschaum endpoints. Tries prod first, then the
/// dev loop (`adb reverse tcp:8899 tcp:8899`), and pins whichever answers.
class Api {
  static const bases = ['https://bwg.mrsm.io', 'http://localhost:8899'];
  String base = bases.first;
  bool _pinned = false;

  final _dio = Dio(BaseOptions(
    connectTimeout: const Duration(seconds: 10),
    receiveTimeout: const Duration(seconds: 60),
    validateStatus: (_) => true,
  ));

  Future<void> _pin() async {
    if (_pinned) return;
    for (final b in bases) {
      try {
        final r = await _dio.get('$b/map-layers/index.json');
        if (r.statusCode == 200) {
          base = b;
          _pinned = true;
          return;
        }
      } catch (_) {}
    }
  }

  Future<dynamic> _get(String path, [Map<String, dynamic>? query]) async {
    await _pin();
    final r = await _dio.get('$base$path', queryParameters: query);
    if ((r.statusCode ?? 500) >= 400) {
      final detail = (r.data is Map) ? (r.data['error'] ?? r.data['detail']) : null;
      throw ApiError(detail?.toString() ?? 'Request failed (${r.statusCode}).');
    }
    return r.data;
  }

  /// GeoJSON layers render straight from URLs in MapLibre; expose the full URL.
  Future<String> layerUrl(String path) async {
    await _pin();
    return '$base$path';
  }

  /// Same layer, fetched inline — used to refresh a source after a submit.
  Future<Map<String, dynamic>> layerGeoJson(String path) async =>
      Map<String, dynamic>.from(await _get(path));

  Future<Map<String, dynamic>> roadInfo(double lat, double lon) async =>
      Map<String, dynamic>.from(
          await _get('/map-layers/road-info', {'lat': lat, 'lon': lon}));

  Future<List<dynamic>> search(String q, {int limit = 8}) async {
    final data = await _get('/map-layers/search', {'q': q, 'limit': limit});
    return List<dynamic>.from(data['results'] ?? []);
  }

  /// Multi-modal directions. [modes] is any of `bike` / `walk` / `transit`;
  /// [roll] switches walking to wheelchair weighting, [bcycle] adds a
  /// bike-share itinerary, and [plan] pins one of the returned alternatives.
  Future<Map<String, dynamic>> route(
    double fromLat,
    double fromLon,
    double toLat,
    double toLon, {
    Set<String> modes = const {'bike'},
    bool roll = false,
    bool bcycle = false,
    String? plan,
  }) async =>
      Map<String, dynamic>.from(await _get('/map-layers/route', {
        'from': '$fromLat,$fromLon',
        'to': '$toLat,$toLon',
        'modes': (modes.isEmpty ? {'bike'} : modes).join(','),
        if (roll) 'roll': '1',
        if (bcycle) 'bcycle': '1',
        'plan': ?plan,
      }));

  /// Bike-share system metadata — the links that hand off to the BCycle app.
  Future<Map<String, dynamic>> bcycleSystem() async =>
      Map<String, dynamic>.from(await _get('/bcycle/system.json'));

  Future<List<dynamic>> walkAuditCategories() async {
    final data = await _get('/walk-audit/categories.json');
    return List<dynamic>.from(data['categories'] ?? []);
  }

  Future<Map<String, dynamic>> submitWalkAudit({
    required String category,
    required String comment,
    required double lat,
    required double lon,
    Uint8List? photoBytes,
    String? photoName,
  }) async {
    await _pin();
    final form = FormData.fromMap({
      'category': category,
      'comment': comment,
      'lat': lat,
      'lon': lon,
      if (photoBytes != null)
        'photo': MultipartFile.fromBytes(photoBytes,
            filename: photoName ?? 'photo.jpg'),
    });
    final r = await _dio.post('$base/walk-audit/submit', data: form);
    if ((r.statusCode ?? 500) >= 400 || r.data is! Map || r.data['ok'] != true) {
      throw ApiError('Could not submit the report. Please try again.');
    }
    return Map<String, dynamic>.from(r.data);
  }

  Future<void> submitBikeParkingFeedback({
    required String spotName,
    required double lat,
    required double lon,
    required String feedback,
    Uint8List? photoBytes,
    String? photoName,
  }) async {
    await _pin();
    final form = FormData.fromMap({
      'spot_name': spotName,
      'lat': lat,
      'lon': lon,
      'feedback': feedback,
      if (photoBytes != null)
        'photo': MultipartFile.fromBytes(photoBytes,
            filename: photoName ?? 'photo.jpg'),
    });
    final r = await _dio.post('$base/bike-parking/submit', data: form);
    if ((r.statusCode ?? 500) >= 400 || r.data is! Map || r.data['ok'] != true) {
      throw ApiError('Could not submit the report. Please try again.');
    }
  }
}

final api = Api();
