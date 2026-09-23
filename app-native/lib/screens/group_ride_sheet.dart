import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:provider/provider.dart';
import 'package:share_plus/share_plus.dart';

import '../api.dart';
import '../auth.dart';
import '../group_ride.dart';
import '../nav.dart';
import '../theme.dart';

/// What the map should do once the sheet closes.
class GroupSheetAction {
  /// `catch-up` (route to [member]), `follow` (navigate the leader's route)
  /// or `fit` (frame everyone).
  final String kind;
  final GroupMember? member;
  const GroupSheetAction(this.kind, [this.member]);
}

/// Start / join / live view of a group ride. [locate] asks for (and returns)
/// the rider's position; joining needs one because everyone shares theirs.
class GroupRideSheet extends StatefulWidget {
  final Future<LatLng?> Function() locate;
  final String? initialCode;
  const GroupRideSheet({super.key, required this.locate, this.initialCode});

  @override
  State<GroupRideSheet> createState() => _GroupRideSheetState();
}

class _GroupRideSheetState extends State<GroupRideSheet> {
  final _rideName = TextEditingController();
  final _riderName = TextEditingController();
  late final _code = TextEditingController(text: widget.initialCode ?? '');
  List<NearbyRide>? _nearby;
  String? _nearbyError;
  Timer? _poll;
  bool _busy = false;
  String? _message;

  GroupRideClient get _client => context.read<GroupRideClient>();

  static const nearbyPollInterval = Duration(seconds: 60);

  @override
  void initState() {
    super.initState();
    final signedIn = context.read<AuthState>().displayName;
    _riderName.text = (signedIn != null && signedIn.isNotEmpty)
        ? signedIn
        : _client.lastRiderName;
    if (!_client.active) {
      unawaited(_refreshNearby());
      // Nearby discovery only while this sheet is open and idle. The backend
      // allows 120 lookups/h per IP; 60 s leaves room for the refresh button.
      _poll = Timer.periodic(nearbyPollInterval, (_) {
        if (mounted && !_client.active) _refreshNearby();
      });
    }
  }

  @override
  void dispose() {
    _poll?.cancel();
    _rideName.dispose();
    _riderName.dispose();
    _code.dispose();
    super.dispose();
  }

  Future<void> _refreshNearby() async {
    final pos = await widget.locate();
    if (!mounted) return;
    if (pos == null) {
      // Don't re-prompt for permission every 10 s.
      _poll?.cancel();
      setState(() {
        _message = _needLocation;
        _nearby = const [];
      });
      return;
    }
    try {
      final rides = await _client.nearby(pos);
      if (mounted) {
        setState(() {
          _nearby = rides;
          _nearbyError = null;
        });
      }
    } on ApiError catch (e) {
      if (!mounted) return;
      setState(() {
        _nearby ??= const [];
        _nearbyError = e.status == 429
            ? 'Too many lookups — try again in a few minutes.'
            : null;
      });
    } catch (_) {
      if (mounted && _nearby == null) setState(() => _nearby = const []);
    }
  }

  static const _needLocation =
      'Location is needed to start or join a group ride: riders see each '
      'other on the map. Turn it on and try again.';

  Future<void> _run(Future<void> Function(LatLng pos) action) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _message = null;
    });
    try {
      final pos = await widget.locate();
      if (pos == null) {
        if (mounted) setState(() => _message = _needLocation);
        return;
      }
      await action(pos);
      _poll?.cancel();
    } on ApiError catch (e) {
      if (mounted) setState(() => _message = e.message);
    } catch (_) {
      if (mounted) {
        setState(() => _message = 'Could not reach the server. Try again.');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final client = context.watch<GroupRideClient>();
    return Padding(
      padding: EdgeInsets.fromLTRB(
        16,
        0,
        16,
        16 + MediaQuery.viewInsetsOf(context).bottom,
      ),
      child: client.ended
          ? _endedView(client)
          : client.active
          ? _activeView(client)
          : _idleView(client),
    );
  }

  Widget _endedView(GroupRideClient client) => Column(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      Semantics(
        liveRegion: true,
        child: Text(
          'The ride has ended',
          style: Theme.of(context).textTheme.titleLarge,
        ),
      ),
      const SizedBox(height: 12),
      FilledButton(
        onPressed: () {
          client.dismissEnded();
          Navigator.pop(context);
        },
        child: const Text('OK'),
      ),
    ],
  );

  Widget _idleView(GroupRideClient client) {
    final theme = Theme.of(context);
    final nearby = _nearby;
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Semantics(
          header: true,
          child: Text('Group ride', style: theme.textTheme.titleLarge),
        ),
        const SizedBox(height: 12),
        TextField(
          key: const ValueKey('group-rider-name'),
          controller: _riderName,
          maxLength: 30,
          textCapitalization: TextCapitalization.words,
          decoration: const InputDecoration(
            labelText: 'Your name (optional)',
            counterText: '',
          ),
        ),
        const SizedBox(height: 16),
        Row(
          children: [
            Expanded(
              child: Semantics(
                header: true,
                child: Text(
                  'Join a ride nearby',
                  style: theme.textTheme.titleSmall,
                ),
              ),
            ),
            IconButton(
              key: const ValueKey('nearby-refresh'),
              tooltip: 'Look again for rides nearby',
              icon: const Icon(Icons.refresh),
              onPressed: nearby == null ? null : _refreshNearby,
            ),
          ],
        ),
        if (_nearbyError != null)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 8),
            child: Semantics(
              liveRegion: true,
              child: Text(
                _nearbyError!,
                style: TextStyle(color: theme.colorScheme.error),
              ),
            ),
          )
        else if (nearby == null)
          const Padding(
            padding: EdgeInsets.all(12),
            child: Center(
              child: CircularProgressIndicator(
                semanticsLabel: 'Looking for rides nearby',
              ),
            ),
          )
        else if (nearby.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text('No rides within 1000 ft right now.'),
          )
        else
          for (final r in nearby)
            MergeSemantics(
              child: ListTile(
                key: ValueKey('nearby-${r.code}'),
                contentPadding: EdgeInsets.zero,
                leading: const Icon(Icons.groups, color: groupRideColor),
                title: Text(r.name),
                subtitle: Text(
                  'Led by ${r.leaderName} · ${r.members} '
                  '${r.members == 1 ? 'rider' : 'riders'} · '
                  '${formatDistance(r.distanceM)} away',
                ),
                trailing: const Text('Join'),
                onTap: _busy
                    ? null
                    : () => _run(
                        (pos) => client.joinByCode(
                          r.code,
                          _riderName.text,
                          pos,
                          rideName: r.name,
                        ),
                      ),
              ),
            ),
        const SizedBox(height: 8),
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: TextField(
                key: const ValueKey('group-code'),
                controller: _code,
                maxLength: 4,
                textCapitalization: TextCapitalization.characters,
                inputFormatters: [
                  FilteringTextInputFormatter.allow(RegExp('[A-Za-z]')),
                  _UpperCase(),
                ],
                decoration: const InputDecoration(
                  labelText: 'Have a code?',
                  counterText: '',
                ),
                onSubmitted: (_) => _joinTypedCode(client),
              ),
            ),
            const SizedBox(width: 8),
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: OutlinedButton(
                onPressed: _busy ? null : () => _joinTypedCode(client),
                child: const Text('Join'),
              ),
            ),
          ],
        ),
        const Divider(height: 32),
        Semantics(
          header: true,
          child: Text('Start a group ride', style: theme.textTheme.titleSmall),
        ),
        TextField(
          key: const ValueKey('group-ride-name'),
          controller: _rideName,
          maxLength: 30,
          textCapitalization: TextCapitalization.sentences,
          decoration: const InputDecoration(
            labelText: 'Ride name',
            hintText: 'Community Roll',
            counterText: '',
          ),
        ),
        const SizedBox(height: 8),
        FilledButton.icon(
          onPressed: _busy
              ? null
              : () => _run(
                  (pos) => client.create(
                    _rideName.text.trim().isEmpty
                        ? 'Community Roll'
                        : _rideName.text,
                    _riderName.text,
                    pos,
                  ),
                ),
          icon: const Icon(Icons.flag_outlined),
          label: const Text('Start and lead'),
        ),
        if (_message != null) ...[
          const SizedBox(height: 12),
          Semantics(liveRegion: true, child: Text(_message!)),
        ],
      ],
    );
  }

  void _joinTypedCode(GroupRideClient client) {
    if (parseRideCode(_code.text) == null) {
      setState(() => _message = 'Ride codes are 4 letters.');
      return;
    }
    _run((pos) => client.joinByCode(_code.text, _riderName.text, pos));
  }

  Widget _activeView(GroupRideClient client) {
    final theme = Theme.of(context);
    final code = client.code ?? '';
    final others = [...client.members]
      ..sort((a, b) => a.isLeader == b.isLeader ? 0 : (a.isLeader ? -1 : 1));
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Expanded(
              child: Semantics(
                header: true,
                child: Text(
                  client.rideName ?? 'Group ride',
                  style: theme.textTheme.titleLarge,
                ),
              ),
            ),
            IconButton(
              tooltip: 'Show everyone on the map',
              icon: const Icon(Icons.zoom_out_map),
              onPressed: () =>
                  Navigator.pop(context, const GroupSheetAction('fit')),
            ),
            IconButton(
              tooltip: 'Share ride link',
              icon: const Icon(Icons.share),
              onPressed: () => SharePlus.instance.share(
                ShareParams(
                  text:
                      'Join my group ride on Bike Walk Greenville: '
                      '${client.shareUrl} (code $code)',
                ),
              ),
            ),
          ],
        ),
        Semantics(
          button: true,
          label: 'Ride code ${code.split('').join(' ')}. Tap to copy.',
          excludeSemantics: true,
          child: InkWell(
            borderRadius: BorderRadius.circular(8),
            onTap: () async {
              await Clipboard.setData(ClipboardData(text: code));
              if (mounted) toast(context, 'Code copied.');
            },
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 4),
              child: Text(
                code,
                style: theme.textTheme.displaySmall?.copyWith(
                  letterSpacing: 8,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 8),
        if (others.isEmpty)
          const Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text('Connecting…'),
          ),
        for (final m in others) _memberRow(client, m),
        if (!client.isLeader && client.leaderRoute != null) ...[
          const SizedBox(height: 8),
          FilledButton.icon(
            onPressed: () =>
                Navigator.pop(context, const GroupSheetAction('follow')),
            icon: const Icon(Icons.navigation_outlined),
            label: const Text('Follow the leader'),
          ),
        ],
        const SizedBox(height: 12),
        OutlinedButton(
          // colorScheme.error: warnRed text was 3.8:1 on the dark sheet.
          style: OutlinedButton.styleFrom(
            foregroundColor: theme.colorScheme.error,
          ),
          onPressed: _busy ? null : () => _leaveOrEnd(client),
          child: Text(client.isLeader ? 'End ride' : 'Leave'),
        ),
        if (_message != null) ...[
          const SizedBox(height: 12),
          Semantics(liveRegion: true, child: Text(_message!)),
        ],
      ],
    );
  }

  Widget _memberRow(GroupRideClient client, GroupMember m) {
    final me = m.id == client.memberId;
    final d = client.distanceTo(m);
    final parts = [
      me ? 'You' : m.name,
      if (m.isLeader) 'leader',
      if (!me && d != null) '${formatDistance(d)} away',
    ];
    // No MergeSemantics: the leading icon is decorative, so the title reads
    // alone and "Catch up" stays its own button node.
    return ListTile(
      key: ValueKey('member-${m.id}'),
      contentPadding: EdgeInsets.zero,
      leading: Icon(
        m.isLeader ? Icons.flag : Icons.person_pin_circle_outlined,
        color: m.isLeader ? groupLeaderColor : groupRideColor,
      ),
      title: Text(parts.join(', ')),
      trailing: me || m.position == null
          ? null
          : TextButton(
              onPressed: () =>
                  Navigator.pop(context, GroupSheetAction('catch-up', m)),
              child: const Text('Catch up'),
            ),
    );
  }

  Future<void> _leaveOrEnd(GroupRideClient client) async {
    final leader = client.isLeader;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(leader ? 'End the ride for everyone?' : 'Leave the ride?'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(leader ? 'End ride' : 'Leave'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      leader ? await client.end() : await client.leave();
      if (mounted) Navigator.pop(context);
    } catch (e) {
      if (mounted) {
        toast(
          context,
          e is ApiError ? e.message : 'Could not reach the server. Try again.',
        );
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }
}

class _UpperCase extends TextInputFormatter {
  @override
  TextEditingValue formatEditUpdate(
    TextEditingValue oldValue,
    TextEditingValue newValue,
  ) => newValue.copyWith(text: newValue.text.toUpperCase());
}
