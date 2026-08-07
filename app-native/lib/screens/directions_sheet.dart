import 'dart:async';

import 'package:flutter/material.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:provider/provider.dart';

import '../api.dart';
import '../app_state.dart';
import '../theme.dart';

/// One end of a trip: a resolved coordinate plus how to name it.
///
/// `isMyLocation` endpoints have no coordinate until the trip is planned — the
/// GPS fix is taken then, so a planner left open for a while doesn't route from
/// a stale position.
class TripEndpoint {
  final String label;
  final LatLng? latLng;
  final bool isMyLocation;

  const TripEndpoint({
    required this.label,
    this.latLng,
    this.isMyLocation = false,
  });

  static const myLocation =
      TripEndpoint(label: 'Your location', isMyLocation: true);

  bool get isEmpty => !isMyLocation && latLng == null;
}

/// What the planner hands back: either a trip to route, or a request to go
/// pick one end by tapping the map.
class DirectionsResult {
  final TripEndpoint? from;
  final TripEndpoint? to;

  /// `from` or `to` when the user chose "pick on map" instead of searching.
  final String? pickField;

  const DirectionsResult({this.from, this.to, this.pickField});

  bool get isPick => pickField != null;
}

/// Trip planner: a start point (your location by default) and a destination,
/// with the travel modes the rider is willing to use.
///
/// Search-to-navigate used to imply "from wherever I am, right now". Making the
/// start an explicit, editable field is what lets someone plan a trip they
/// aren't standing at the beginning of.
class DirectionsSheet extends StatefulWidget {
  final TripEndpoint from;
  final TripEndpoint to;

  const DirectionsSheet({super.key, required this.from, required this.to});

  @override
  State<DirectionsSheet> createState() => _DirectionsSheetState();
}

class _DirectionsSheetState extends State<DirectionsSheet> {
  late TripEndpoint _from = widget.from;
  late TripEndpoint _to = widget.to;

  final _fromCtl = TextEditingController();
  final _toCtl = TextEditingController();

  /// Which field is being searched, and its current results.
  String? _searching;
  List<dynamic> _results = [];
  Timer? _debounce;
  bool _loading = false;

  @override
  void initState() {
    super.initState();
    _fromCtl.text = _from.isMyLocation ? '' : _from.label;
    _toCtl.text = _to.isEmpty ? '' : _to.label;
  }

  @override
  void dispose() {
    _debounce?.cancel();
    _fromCtl.dispose();
    _toCtl.dispose();
    super.dispose();
  }

  void _onChanged(String field, String q) {
    _debounce?.cancel();
    setState(() {
      _searching = field;
      if (q.trim().length < 2) _results = [];
    });
    if (q.trim().length < 2) return;
    setState(() => _loading = true);
    _debounce = Timer(const Duration(milliseconds: 400), () async {
      try {
        final results = await api.search(q.trim());
        if (mounted) setState(() => _results = results);
      } catch (_) {
        if (mounted) setState(() => _results = []);
      } finally {
        if (mounted) setState(() => _loading = false);
      }
    });
  }

  void _select(String field, Map<String, dynamic> r) {
    final lat = (r['lat'] as num?)?.toDouble();
    final lon = (r['lon'] as num?)?.toDouble();
    if (lat == null || lon == null) return;
    final endpoint = TripEndpoint(
      label: r['label']?.toString() ?? 'Selected place',
      latLng: LatLng(lat, lon),
    );
    setState(() {
      if (field == 'from') {
        _from = endpoint;
        _fromCtl.text = endpoint.label;
      } else {
        _to = endpoint;
        _toCtl.text = endpoint.label;
      }
      _results = [];
      _searching = null;
    });
    FocusScope.of(context).unfocus();
  }

  void _useMyLocation(String field) {
    setState(() {
      if (field == 'from') {
        _from = TripEndpoint.myLocation;
        _fromCtl.clear();
      } else {
        _to = TripEndpoint.myLocation;
        _toCtl.clear();
      }
      _results = [];
      _searching = null;
    });
    FocusScope.of(context).unfocus();
  }

  void _swap() {
    setState(() {
      final f = _from, t = _to;
      _from = t;
      _to = f;
      _fromCtl.text = _from.isMyLocation || _from.isEmpty ? '' : _from.label;
      _toCtl.text = _to.isMyLocation || _to.isEmpty ? '' : _to.label;
      _results = [];
      _searching = null;
    });
  }

  bool get _ready =>
      (_from.isMyLocation || _from.latLng != null) &&
      (_to.isMyLocation || _to.latLng != null) &&
      !(_from.isMyLocation && _to.isMyLocation);

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: SafeArea(
        child: SingleChildScrollView(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Directions',
                    style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 12),
                Row(
                  children: [
                    Expanded(
                      child: Column(
                        children: [
                          _endpointField(
                            field: 'from',
                            controller: _fromCtl,
                            endpoint: _from,
                            icon: Icons.trip_origin,
                            hint: 'Your location',
                          ),
                          const SizedBox(height: 8),
                          _endpointField(
                            field: 'to',
                            controller: _toCtl,
                            endpoint: _to,
                            icon: Icons.place,
                            hint: 'Choose destination',
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      tooltip: 'Swap start and destination',
                      icon: const Icon(Icons.swap_vert),
                      onPressed: _swap,
                    ),
                  ],
                ),
                if (_loading)
                  const Padding(
                    padding: EdgeInsets.only(top: 10),
                    child: LinearProgressIndicator(minHeight: 2),
                  ),
                if (_results.isNotEmpty && _searching != null)
                  ConstrainedBox(
                    constraints: const BoxConstraints(maxHeight: 260),
                    child: ListView(
                      shrinkWrap: true,
                      children: [
                        for (final r in _results.take(8))
                          ListTile(
                            dense: true,
                            leading: const Icon(Icons.place_outlined),
                            title: Text(r['label']?.toString() ?? ''),
                            subtitle: Text(r['sublabel']?.toString() ?? ''),
                            onTap: () => _select(
                                _searching!, Map<String, dynamic>.from(r as Map)),
                          ),
                      ],
                    ),
                  ),
                const SizedBox(height: 16),
                Text('How are you travelling?',
                    style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: 8),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final m in TravelMode.values)
                      FilterChip(
                        avatar: Icon(state.iconFor(m), size: 18),
                        label: Text(state.labelFor(m)),
                        selected: state.modes.contains(m),
                        onSelected: (_) => state.toggleMode(m),
                      ),
                  ],
                ),
                const SizedBox(height: 4),
                Text(
                  'Pick more than one and we compare them — bike to the bus, '
                  'walk the rest.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
                SwitchListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  secondary: const Icon(Icons.accessible_forward),
                  title: const Text('I use a wheelchair'),
                  subtitle: const Text(
                      'Strongly prefers routes with sidewalks and flags the gaps'),
                  value: state.roll,
                  onChanged: state.setRoll,
                ),
                SwitchListTile(
                  dense: true,
                  contentPadding: EdgeInsets.zero,
                  secondary: Icon(Icons.pedal_bike, color: hexColor(bcycleRed)),
                  title: const Text('Include BCycle bike share'),
                  subtitle: const Text(
                      'Walk to a dock, ride a rental, dock it near the end'),
                  value: state.useBcycle,
                  onChanged: state.setUseBcycle,
                ),
                if (state.showsBikeOptions) ...[
                  SwitchListTile(
                    dense: true,
                    contentPadding: EdgeInsets.zero,
                    secondary: const Icon(Icons.electric_bike),
                    title: const Text('I ride an e-bike'),
                    subtitle: const Text(
                        'Faster, and hills cost you a lot less'),
                    value: state.useEbike,
                    onChanged: state.setUseEbike,
                  ),
                  const SizedBox(height: 4),
                  Text('How much traffic is OK?',
                      style: Theme.of(context).textTheme.labelLarge),
                  const SizedBox(height: 6),
                  SegmentedButton<BikeStress>(
                    segments: [
                      for (final level in BikeStress.values)
                        ButtonSegment(
                          value: level,
                          label: Text(bikeStressLabels[level]!),
                        ),
                    ],
                    selected: {state.stress},
                    showSelectedIcon: false,
                    onSelectionChanged: (sel) => state.setStress(sel.first),
                  ),
                  const SizedBox(height: 4),
                  Text(
                    bikeStressBlurbs[state.stress]!,
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ],
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  child: FilledButton.icon(
                    style: FilledButton.styleFrom(
                      backgroundColor: brandGreen,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                    ),
                    icon: const Icon(Icons.directions),
                    label: const Text('Get directions'),
                    onPressed: _ready
                        ? () => Navigator.pop(
                              context,
                              DirectionsResult(from: _from, to: _to),
                            )
                        : null,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _endpointField({
    required String field,
    required TextEditingController controller,
    required TripEndpoint endpoint,
    required IconData icon,
    required String hint,
  }) {
    final showingMyLocation = endpoint.isMyLocation;
    return TextField(
      controller: controller,
      onChanged: (q) => _onChanged(field, q),
      onTap: () => setState(() => _searching = field),
      decoration: InputDecoration(
        isDense: true,
        prefixIcon: Icon(icon, color: brandGreen, size: 20),
        hintText: showingMyLocation ? 'Your location' : hint,
        hintStyle: showingMyLocation
            ? const TextStyle(fontStyle: FontStyle.italic)
            : null,
        filled: true,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: BorderSide.none,
        ),
        suffixIcon: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            IconButton(
              tooltip: 'Use my location',
              visualDensity: VisualDensity.compact,
              icon: Icon(Icons.my_location,
                  size: 18,
                  color: showingMyLocation ? brandGreen : Colors.black45),
              onPressed: () => _useMyLocation(field),
            ),
            IconButton(
              tooltip: 'Pick on the map',
              visualDensity: VisualDensity.compact,
              icon: const Icon(Icons.touch_app_outlined, size: 18),
              onPressed: () => Navigator.pop(
                context,
                DirectionsResult(from: _from, to: _to, pickField: field),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
