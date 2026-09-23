import 'dart:typed_data';
import 'dart:convert';

import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart' show kIsWeb;

class ApiError implements Exception {
  final String message;
  final int? status;
  ApiError(this.message, [this.status]);
  @override
  String toString() => message;
}

/// A signed-in request came back 401: the token is gone server-side. [Api]
/// has already cleared it (and told [Api.onUnauthorized]); `withAuth` in
/// auth.dart re-prompts once.
class AuthExpired extends ApiError {
  AuthExpired() : super('Your sign-in expired. Please sign in again.', 401);
}

/// True for an [AuthExpired], bare or wrapped in the DioException the
/// interceptor rejects with.
bool isAuthExpired(Object e) =>
    e is AuthExpired || (e is DioException && e.error is AuthExpired);

/// Whether a request to [uri] may carry the account token: only the https
/// prod origin (or the page's own origin on web), never the localhost dev
/// fallback, and never `/group-rides/*` (anonymous by design; its 401s mean
/// a revoked member token, not an expired account).
bool sendsBearer(Uri uri) =>
    (uri.origin == Api.bases.first ||
        (kIsWeb && uri.scheme == 'https' && uri.origin == Uri.base.origin)) &&
    !uri.path.startsWith('/group-rides');

/// Thin client for the BWG Meerschaum endpoints. Tries prod first, then the
/// dev loop (`adb reverse tcp:8899 tcp:8899`), and pins whichever answers.
class Api {
  static const bases = ['https://bwg.mrsm.io', 'http://localhost:8899'];
  String base = bases.first;
  bool _pinned = false;

  /// Signed-in user's token; sent as `Authorization: Bearer` when set.
  /// AuthState keeps it in sync with secure storage.
  String? bearerToken;

  /// Called once when a request sent WITH a token gets a 401 (revoked or
  /// expired); AuthState signs out locally.
  void Function()? onUnauthorized;

  late final _dio = Dio(
    BaseOptions(
      connectTimeout: const Duration(seconds: 10),
      receiveTimeout: const Duration(seconds: 60),
      validateStatus: (_) => true,
    ),
  )..interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) {
          final token = bearerToken;
          if (token != null && sendsBearer(options.uri)) {
            options.headers['Authorization'] = 'Bearer $token';
          }
          handler.next(options);
        },
        onResponse: (r, handler) {
          final sent = r.requestOptions.headers['Authorization'];
          if (r.statusCode == 401 && sent != null && bearerToken != null &&
              sent == 'Bearer $bearerToken') {
            bearerToken = null;
            onUnauthorized?.call();
            handler.reject(
              DioException(
                requestOptions: r.requestOptions,
                response: r,
                error: AuthExpired(),
              ),
            );
            return;
          }
          handler.next(r);
        },
      ),
    );

  /// JSON request; >= 400 throws [ApiError] with the backend's message.
  Future<dynamic> _send(String method, String path, {Object? data}) async {
    await _pin();
    final r = await _dio.request(
      '$base$path',
      data: data,
      options: Options(method: method),
    );
    if ((r.statusCode ?? 500) >= 400) {
      final detail = (r.data is Map)
          ? (r.data['error'] ?? r.data['detail'])
          : null;
      throw ApiError(
        detail?.toString() ?? 'Request failed (${r.statusCode}).',
        r.statusCode,
      );
    }
    return r.data;
  }

  // ------------------------------------------------------------- accounts

  /// Emails a 6-digit sign-in code. 400 bad email, 429 rate limited.
  Future<void> requestCode(String email) =>
      _send('POST', '/bwg/auth/request-code', data: {'email': email});

  /// `{token, email, display_name, is_admin}`; 401 wrong/expired code.
  Future<Map<String, dynamic>> verifyCode(String email, String code) async =>
      Map<String, dynamic>.from(
        await _send(
          'POST',
          '/bwg/auth/verify',
          data: {'email': email, 'code': code},
        ),
      );

  /// `{email, display_name, is_admin, disclaimer_version, settings, …}`.
  Future<Map<String, dynamic>> me() async =>
      Map<String, dynamic>.from(await _send('GET', '/bwg/auth/me'));

  /// Any of `display_name`, `settings`, `disclaimer_version`; same shape back.
  Future<Map<String, dynamic>> updateMe(Map<String, dynamic> body) async =>
      Map<String, dynamic>.from(await _send('PUT', '/bwg/auth/me', data: body));

  /// Invalidates this device's token server-side.
  Future<void> signOut() => _send('DELETE', '/bwg/auth/token');

  /// Admin only: `{photos, held}` awaiting review.
  Future<Map<String, dynamic>> moderationPendingCount() async =>
      Map<String, dynamic>.from(
        await _send('GET', '/bwg/moderation/pending-count'),
      );

  /// The signed-in rider's saved routes, newest first.
  Future<List<Map<String, dynamic>>> savedRoutes() async {
    final data = await _send('GET', '/bwg/routes');
    return [
      for (final r in (data is Map ? data['routes'] : null) ?? const [])
        if (r is Map) Map<String, dynamic>.from(r),
    ];
  }

  /// `{name, from_lat, from_lon, to_lat, to_lon, modes, stress?, distance_m?,
  /// duration_min?, geometry?}` → the saved row (with `id`). 409 at 200.
  Future<Map<String, dynamic>> saveRoute(Map<String, dynamic> body) async =>
      Map<String, dynamic>.from(await _send('POST', '/bwg/routes', data: body));

  Future<void> deleteSavedRoute(String id) =>
      _send('DELETE', '/bwg/routes/${Uri.encodeComponent(id)}');

  // ----------------------------------------------------------- group rides

  /// Start a ride (caller leads) → `{code, ride_id, member_id, member_token,
  /// share_url}`.
  Future<Map<String, dynamic>> createGroupRide(
    String name,
    String riderName,
    double lat,
    double lon,
  ) async => Map<String, dynamic>.from(
    await _send(
      'POST',
      '/group-rides',
      data: {'name': name, 'rider_name': riderName, 'lat': lat, 'lon': lon},
    ),
  );

  /// Rides whose leader is within 305 m: `[{code, name, leader_name,
  /// distance_m, members}]`, nearest first.
  Future<List<Map<String, dynamic>>> nearbyGroupRides(
    double lat,
    double lon,
  ) async {
    final data = await _get('/group-rides/nearby', {'lat': lat, 'lon': lon});
    return [
      for (final r in data is List ? data : const [])
        if (r is Map) Map<String, dynamic>.from(r),
    ];
  }

  /// `{ride_id, member_id, member_token, name}`; 404 unknown/ended.
  Future<Map<String, dynamic>> joinGroupRide(
    String code,
    String riderName,
    double lat,
    double lon,
  ) async => Map<String, dynamic>.from(
    await _send(
      'POST',
      '/group-rides/${Uri.encodeComponent(code)}/join',
      data: {'rider_name': riderName, 'lat': lat, 'lon': lon},
    ),
  );

  /// Push my position, pull everyone's: `{ended, leader_id, route_rev,
  /// route?, members}`; `route` only comes back when [routeRev] is stale.
  Future<Map<String, dynamic>> pingGroupRide(
    String code,
    String token,
    double lat,
    double lon, {
    double? heading,
    double? speed,
    int? routeRev,
  }) async => Map<String, dynamic>.from(
    await _send(
      'POST',
      '/group-rides/${Uri.encodeComponent(code)}/ping',
      data: {
        'member_token': token,
        'lat': lat,
        'lon': lon,
        'heading': ?heading,
        'speed': ?speed,
        'route_rev': ?routeRev,
      },
    ),
  );

  /// Leader only: the route everyone follows (a GeoJSON LineString Feature),
  /// or null to clear it.
  Future<void> setGroupRoute(
    String code,
    String token,
    Map<String, dynamic>? route,
  ) => _send(
    'PUT',
    '/group-rides/${Uri.encodeComponent(code)}/route',
    data: {'member_token': token, 'route': route},
  );

  /// Leave (the leader leaving ends the ride).
  Future<void> leaveGroupRide(String code, String token) => _send(
    'POST',
    '/group-rides/${Uri.encodeComponent(code)}/leave',
    data: {'member_token': token},
  );

  /// Leader only.
  Future<void> endGroupRide(String code, String token) => _send(
    'POST',
    '/group-rides/${Uri.encodeComponent(code)}/end',
    data: {'member_token': token},
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
      throw ApiError(
        detail?.toString() ?? 'Request failed (${r.statusCode}).',
        r.statusCode,
      );
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
  /// Published with immutable history and community rollback. Returns the
  /// server reply: `{ok, id, status: 'held'|'published', photo_status}`.
  Future<Map<String, dynamic>> submitPoint({
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
    return Map<String, dynamic>.from(r.data);
  }

  Future<List<Map<String, dynamic>>> communityHistory() async {
    await _pin();
    final response = await _dio.get('$base/map-layers/community/history');
    return List<Map<String, dynamic>>.from(response.data['revisions']);
  }

  /// Thumbs up/down on a community contribution. Posting the same value again
  /// removes the vote. Returns `{up, down, mine}` (`mine`: 'up'|'down'|null).
  Future<Map<String, dynamic>> vote(String id, bool up) async {
    await _pin();
    final r = await _dio.post(
      '$base/map-layers/community/vote',
      data: {'id': id, 'up': up},
    );
    if ((r.statusCode ?? 500) >= 400 || r.data is! Map) {
      final detail = (r.data is Map)
          ? (r.data['error'] ?? r.data['detail'])
          : null;
      throw ApiError(detail?.toString() ?? 'Vote was not saved. Please try again.');
    }
    return Map<String, dynamic>.from(r.data);
  }

  /// Upcoming BWG calendar events, soonest first.
  Future<List<Map<String, dynamic>>> events({int days = 60}) async {
    final data = await _get('/bwg/events.json', {'days': days});
    return [
      for (final e in (data is Map ? data['events'] : null) ?? const [])
        if (e is Map) Map<String, dynamic>.from(e),
    ];
  }

  Future<void> rollbackContribution(String id, String reason) =>
      rollbackContributions([id], reason);

  /// Remove several contributions (whole edit chains) with one reason, in one
  /// request — the server's hourly change limit counts requests, not ids.
  Future<void> rollbackContributions(List<String> ids, String reason) async {
    await _pin();
    final response = await _dio.post(
      '$base/map-layers/community/rollback',
      data: {'ids': ids, 'id': ids.first, 'reason': reason},
    );
    if (response.data is! Map || response.data['ok'] != true) {
      final detail = (response.data is Map) ? response.data['error'] : null;
      throw ApiError(detail?.toString() ?? 'Rollback was not saved. Please try again.');
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

/// Shared client. Not final so tests can swap in a fake.
Api api = Api();
