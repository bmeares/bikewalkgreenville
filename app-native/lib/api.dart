import 'dart:typed_data';
import 'dart:convert';

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

  final _dio = Dio(
    BaseOptions(
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 60),
      validateStatus: (_) => true,
    ),
  );

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
      final detail = (r.data is Map)
          ? (r.data['error'] ?? r.data['detail'])
          : null;
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
        await _get('/map-layers/road-info', {'lat': lat, 'lon': lon}),
      );

  Future<List<dynamic>> search(String q, {int limit = 8}) async {
    final data = await _get('/map-layers/search', {'q': q, 'limit': limit});
    return List<dynamic>.from(data['results'] ?? []);
  }

  /// Multi-modal directions. [modes] is any of `bike` / `walk` / `transit`;
  /// [roll] switches walking to wheelchair weighting, [bcycle] adds a
  /// bike-share itinerary, and [plan] pins one of the returned alternatives.
  ///
  /// [ebike] rides at e-bike pace and shrugs off hills; [stress] is how much
  /// traffic the rider will accept (`quiet` / `balanced` / `direct`). Both
  /// apply to every pedalling leg, including the ride to a bus stop. Omitting
  /// them is the server's historical behaviour.
  ///
  /// [alt] (with [plan] pinned to a plain bike/walk/roll plan) asks for that
  /// plan's Nth alternate route; `alt_distinct: false` in the response means
  /// no genuinely different way exists.
  Future<Map<String, dynamic>> route(
    double fromLat,
    double fromLon,
    double toLat,
    double toLon, {
    Set<String> modes = const {'bike'},
    bool roll = false,
    bool bcycle = false,
    String? plan,
    bool ebike = false,
    String? stress,
    int alt = 0,
    bool trail = true,
    bool community = true,
  }) async => Map<String, dynamic>.from(
    await _get('/map-layers/route', {
      'from': '$fromLat,$fromLon',
      'to': '$toLat,$toLon',
      'modes': (modes.isEmpty ? {'bike'} : modes).join(','),
      if (roll) 'roll': '1',
      if (bcycle) 'bcycle': '1',
      if (ebike) 'ebike': '1',
      if (alt > 0) 'alt': '$alt',
      // Default on: the trail bias is the app's personality. Off prices the
      // Swamp Rabbit Trail like any calm street.
      if (!trail) 'trail': '0',
      if (!community) 'community': '0',
      'stress': ?stress,
      'plan': ?plan,
    }),
  );

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
        'photo': MultipartFile.fromBytes(
          photoBytes,
          filename: photoName ?? 'photo.jpg',
        ),
    });
    final r = await _dio.post('$base/walk-audit/submit', data: form);
    if ((r.statusCode ?? 500) >= 400 ||
        r.data is! Map ||
        r.data['ok'] != true) {
      throw ApiError('Could not submit the report. Please try again.');
    }
    return Map<String, dynamic>.from(r.data);
  }

  /// A missing point on the map (bike rack, repair station, fountain…).
  /// Published with immutable history and community rollback.
  Future<void> submitPoint({
    required String category,
    required String name,
    required String comment,
    required double lat,
    required double lon,
    Uint8List? photoBytes,
    String? photoName,
    Map<String, dynamic>? geometry,
    String? replaces,
  }) async {
    await _pin();
    final form = FormData.fromMap({
      'category': category,
      'replaces': ?replaces,
      if (geometry != null) 'geometry': jsonEncode(geometry),
      'name': name,
      'comment': comment,
      'lat': lat,
      'lon': lon,
      if (photoBytes != null)
        'photo': MultipartFile.fromBytes(
          photoBytes,
          filename: photoName ?? 'photo.jpg',
        ),
    });
    final r = await _dio.post('$base/map-layers/submit-point', data: form);
    if ((r.statusCode ?? 500) >= 400 ||
        r.data is! Map ||
        r.data['ok'] != true) {
      final detail = (r.data is Map) ? r.data['error'] : null;
      throw ApiError(
        detail?.toString() ?? 'Could not submit the place. Please try again.',
      );
    }
  }

  Future<List<Map<String, dynamic>>> communityHistory() async {
    await _pin();
    final response = await _dio.get('$base/map-layers/community/history');
    return List<Map<String, dynamic>>.from(response.data['revisions']);
  }

  /// "I rode this, it exists." Returns the new confirmation count.
  Future<int> confirmContribution(String id, String voter) async {
    await _pin();
    final response = await _dio.post(
      '$base/map-layers/community/confirm',
      data: {'id': id, 'voter': voter},
    );
    if (response.data is! Map || response.data['ok'] != true) {
      final detail = (response.data is Map) ? response.data['error'] : null;
      throw ApiError(detail?.toString() ?? 'Vote was not saved. Please try again.');
    }
    return (response.data['confirmations'] as num?)?.toInt() ?? 0;
  }

  Future<void> rollbackContribution(String id, String reason) async {
    await _pin();
    final response = await _dio.post(
      '$base/map-layers/community/rollback',
      data: {'id': id, 'reason': reason},
    );
    if (response.data is! Map || response.data['ok'] != true) {
      throw ApiError('Rollback was not saved. Please try again.');
    }
  }

  /// Reports and dismissals from walk-audit, same row shape as
  /// [communityHistory] (type, ts_display, geometry, active).
  Future<List<Map<String, dynamic>>> walkAuditHistory() async {
    await _pin();
    final response = await _dio.get('$base/walk-audit/history');
    return List<Map<String, dynamic>>.from(response.data['edits']);
  }

  /// Removes a reported issue from the map; the dismissal is public history.
  Future<void> dismissReport(String id, String reason) async {
    await _pin();
    final response = await _dio.post(
      '$base/walk-audit/dismiss',
      data: {'id': id, 'reason': reason},
    );
    if (response.data is! Map || response.data['ok'] != true) {
      final detail = response.data is Map ? response.data['error'] : null;
      throw ApiError(
        detail?.toString() ?? 'Dismissal was not saved. Please try again.',
      );
    }
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
        'photo': MultipartFile.fromBytes(
          photoBytes,
          filename: photoName ?? 'photo.jpg',
        ),
    });
    final r = await _dio.post('$base/bike-parking/submit', data: form);
    if ((r.statusCode ?? 500) >= 400 ||
        r.data is! Map ||
        r.data['ok'] != true) {
      throw ApiError('Could not submit the report. Please try again.');
    }
  }
}

final api = Api();
