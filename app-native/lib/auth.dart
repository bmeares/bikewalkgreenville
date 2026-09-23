import 'dart:async';
import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api.dart';
import 'app_state.dart';
import 'widgets/app_sheet.dart';
import 'widgets/sign_in_sheet.dart';

/// Where the session lives between launches: one JSON string.
abstract class AuthStore {
  Future<String?> read();
  Future<void> write(String? value);
}

/// Keychain / Keystore on devices, the plugin's WebCrypto store on web.
class SecureAuthStore implements AuthStore {
  static const _key = 'bwg_auth';
  final _storage = const FlutterSecureStorage();
  @override
  Future<String?> read() => _storage.read(key: _key);
  @override
  Future<void> write(String? value) => value == null
      ? _storage.delete(key: _key)
      : _storage.write(key: _key, value: value);
}

/// The signed-in account (passwordless email code → long-lived token).
/// Browsing, routing and recording never need it; public writes do.
class AuthState extends ChangeNotifier {
  AuthState({AuthStore? store}) : _store = store ?? SecureAuthStore();

  final AuthStore _store;
  AppState? _app;

  String? _token;
  String? _email;
  String? _displayName;
  bool _isAdmin = false;
  List<Map<String, dynamic>> _savedRoutes = [];

  String? get token => _token;
  String? get email => _email;
  String? get displayName => _displayName;
  bool get isAdmin => _isAdmin;
  bool get signedIn => _token != null;

  /// Newest first; empty when signed out.
  List<Map<String, dynamic>> get savedRoutes => _savedRoutes;

  // Settings sync: nothing is pushed until the account's settings were
  // merged in once, so a fresh device never overwrites the account.
  bool _synced = false;
  String? _lastPushed;
  Timer? _pushTimer;
  static const pushDelay = Duration(seconds: 2);

  /// Wire the preferences that sync with the account.
  void attach(AppState app) {
    _app?.removeListener(_onAppChanged);
    _app = app..addListener(_onAppChanged);
  }

  void _syncApi() {
    api.bearerToken = _token;
    api.onUnauthorized = _expired;
  }

  Future<void> load() async {
    _syncApi();
    Map<String, dynamic>? saved;
    try {
      final raw = await _store.read();
      if (raw != null) saved = Map<String, dynamic>.from(jsonDecode(raw));
    } catch (_) {
      // Unreadable storage (web without WebCrypto, a wiped keystore):
      // signed out is a fine answer.
    }
    if (saved == null || saved['token'] is! String) return;
    _token = saved['token'];
    _email = saved['email']?.toString();
    _displayName = saved['display_name']?.toString();
    _isAdmin = saved['is_admin'] == true;
    _syncApi();
    notifyListeners();
    await refresh();
  }

  /// Re-read `/me` (admin flag, display name, settings). A 401 has already
  /// signed us out through [Api.onUnauthorized]; offline keeps the session.
  Future<void> refresh() async {
    if (!signedIn) return;
    try {
      final me = await api.me();
      _applyProfile(me);
      await _mergeSettings(me['settings'], union: false);
      await refreshSavedRoutes();
    } catch (_) {}
  }

  /// After `/bwg/auth/verify`: keep the token, pull the account's settings,
  /// push the merged result plus the accepted disclaimer version.
  Future<void> completeSignIn(Map<String, dynamic> verified) async {
    _token = verified['token']?.toString();
    _email = verified['email']?.toString();
    _displayName = verified['display_name']?.toString();
    _isAdmin = verified['is_admin'] == true;
    _synced = false;
    _syncApi();
    await _persist();
    notifyListeners();
    try {
      final me = await api.me();
      _applyProfile(me);
      await _mergeSettings(me['settings']);
      await _pushNow();
    } catch (_) {}
    unawaited(refreshSavedRoutes());
  }

  Future<void> setDisplayName(String name) async {
    final me = await api.updateMe({'display_name': name.trim()});
    _applyProfile(me);
  }

  /// Sign out this device: revoke server-side (best effort), forget locally.
  Future<void> signOut() async {
    try {
      await api.signOut();
    } catch (_) {}
    await _clear();
  }

  void _expired() => unawaited(_clear());

  Future<void> _clear() async {
    _pushTimer?.cancel();
    _token = _email = _displayName = null;
    _isAdmin = false;
    _synced = false;
    _lastPushed = null;
    _savedRoutes = [];
    _syncApi();
    notifyListeners();
    try {
      await _store.write(null);
    } catch (_) {}
  }

  void _applyProfile(Map<String, dynamic> me) {
    _displayName = me['display_name']?.toString();
    _isAdmin = me['is_admin'] == true;
    if (me['email'] != null) _email = me['email'].toString();
    notifyListeners();
    unawaited(_persist());
  }

  Future<void> _persist() async {
    try {
      await _store.write(
        jsonEncode({
          'token': _token,
          'email': _email,
          'display_name': _displayName,
          'is_admin': _isAdmin,
        }),
      );
    } catch (_) {}
  }

  /// [union]: only the first merge after sign-in unions saved places.
  Future<void> _mergeSettings(dynamic remote, {bool union = true}) async {
    final app = _app;
    if (app == null) return;
    await app.loaded;
    if (remote is Map && remote.isNotEmpty) {
      app.mergeRemoteSettings(Map<String, dynamic>.from(remote), union: union);
    }
    _synced = true;
    if (jsonEncode(app.syncedSettings) != jsonEncode(remote)) {
      _schedulePush();
    } else {
      _lastPushed = jsonEncode(remote);
    }
  }

  void _onAppChanged() {
    if (!signedIn || !_synced || _app == null) return;
    if (jsonEncode(_app!.syncedSettings) == _lastPushed) return;
    _schedulePush();
  }

  void _schedulePush() {
    _pushTimer?.cancel();
    _pushTimer = Timer(pushDelay, () => unawaited(_pushNow()));
  }

  Future<void> _pushNow() async {
    final app = _app;
    if (!signedIn || app == null) return;
    _pushTimer?.cancel();
    final settings = app.syncedSettings;
    try {
      await api.updateMe({
        'settings': settings,
        if (app.disclaimerAcceptedVersion > 0)
          'disclaimer_version': app.disclaimerAcceptedVersion,
      });
      _lastPushed = jsonEncode(settings);
    } catch (_) {
      // Next change retries.
    }
  }

  // ----------------------------------------------------------- saved routes

  Future<void> refreshSavedRoutes() async {
    if (!signedIn) return;
    try {
      _savedRoutes = await api.savedRoutes();
      notifyListeners();
    } catch (_) {}
  }

  Future<Map<String, dynamic>> saveRoute(Map<String, dynamic> body) async {
    final saved = await api.saveRoute(body);
    _savedRoutes = [
      saved,
      ..._savedRoutes.where((r) => r['id'] != saved['id']),
    ];
    notifyListeners();
    return saved;
  }

  Future<void> deleteSavedRoute(String id) async {
    await api.deleteSavedRoute(id);
    _savedRoutes = [..._savedRoutes.where((r) => r['id']?.toString() != id)];
    notifyListeners();
  }

  @override
  void dispose() {
    _pushTimer?.cancel();
    _app?.removeListener(_onAppChanged);
    super.dispose();
  }
}

/// The app's account. Not final so tests can swap in one with a fake store.
AuthState auth = AuthState();

/// Gate for anything public (votes, submissions). Returns true when the user
/// is signed in (or has just signed in), false to abort the action.
class AuthGate {
  /// Swappable so widget tests can skip the sheet.
  static Future<bool> Function(BuildContext) require = _require;

  static Future<bool> _require(BuildContext context) async {
    if (auth.signedIn) return true;
    final done = await showAppSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => const SignInSheet(),
    );
    return done == true && auth.signedIn;
  }
}

/// Gate, run [action], and when the token turns out to be dead (401), sign
/// in again once and retry. Null when the rider declined to sign in.
Future<T?> withAuth<T>(
  BuildContext context,
  Future<T> Function() action,
) async {
  for (var attempt = 0; ; attempt++) {
    if (!context.mounted || !await AuthGate.require(context)) return null;
    try {
      return await action();
    } catch (e) {
      if (attempt > 0 || !isAuthExpired(e)) rethrow;
    }
  }
}

/// Toast for a community submission reply (`status`, `photo_status`).
String submittedMessage(
  Map<String, dynamic>? reply, {
  required String published,
}) {
  final held = reply?['status'] == 'held';
  final photo = reply?['photo_status'] == 'pending';
  return [
    held
        ? 'Thanks — your submission is awaiting review before it appears.'
        : published,
    if (photo) 'Photos appear after BWG approves them.',
  ].join(' ');
}
