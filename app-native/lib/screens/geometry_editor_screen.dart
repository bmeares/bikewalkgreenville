import 'dart:math' as math;
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:pointer_interceptor/pointer_interceptor.dart';
import '../geometry_draft.dart';
import '../theme.dart';
import '../widgets/app_sheet.dart';
import 'add_point_sheet.dart';

enum DrawTool { pan, vertex, move, insert, curve, pen, erase }

class GeometryEditorScreen extends StatefulWidget {
  final LatLng center;
  final String style;
  final Map<String, dynamic>? geometry;
  final bool polygon;
  final String? name, comment, category, replaces;
  const GeometryEditorScreen({
    super.key,
    required this.center,
    required this.style,
    this.geometry,
    this.polygon = false,
    this.name,
    this.comment,
    this.category,
    this.replaces,
  });
  @override
  State<GeometryEditorScreen> createState() => _GeometryEditorScreenState();
}

class _GeometryEditorScreenState extends State<GeometryEditorScreen> {
  late final GeometryDraft _draft;
  MapLibreMapController? _map;
  DrawTool _tool = DrawTool.vertex;
  int? _selected, _curveSegment;
  bool _fromStart = false, _publishing = false, _ready = false;
  int _projectionSeq = 0;
  List<Offset> _screen = [], _strokeScreen = [];
  final List<LatLng> _stroke = [];
  Future<void> _queue = Future.value();
  bool _dragging = false;
  final _coordinateControllers = <TextEditingController>[];

  @override
  void dispose() {
    for (final c in _coordinateControllers) {
      c.dispose();
    }
    super.dispose();
  }

  double get _ratio =>
      !kIsWeb && defaultTargetPlatform == TargetPlatform.android
      ? MediaQuery.of(context).devicePixelRatio
      : 1;

  @override
  void initState() {
    super.initState();
    final geometry = widget.geometry;
    final raw = geometry == null
        ? []
        : geometry['type'] == 'Polygon'
        ? geometry['coordinates'][0] as List
        : geometry['coordinates'] as List;
    _draft = GeometryDraft(
      raw.map(
        (p) => LatLng((p[1] as num).toDouble(), (p[0] as num).toDouble()),
      ),
      polygon: widget.polygon,
    );
  }

  Future<void> _project() async {
    if (!_ready || !mounted) return;
    final seq = ++_projectionSeq;
    final ratio = _ratio;
    final pts = await _map!.toScreenLocationBatch(_draft.points);
    if (mounted && seq == _projectionSeq) {
      setState(() {
        _screen = pts.map((p) => Offset(p.x / ratio, p.y / ratio)).toList();
      });
    }
  }

  Future<LatLng> _position(Offset p) =>
      _map!.toLatLng(math.Point(p.dx * _ratio, p.dy * _ratio));
  void _enqueue(Future<void> Function() work) {
    _queue = _queue.then((_) async {
      if (!_ready || !mounted) return;
      try {
        await work();
      } catch (e) {
        if (mounted) {
          toast(context, e.toString().replaceFirst('Bad state: ', ''));
        }
      }
    });
  }

  Future<void> _changed() async {
    if (_selected != null && _selected! >= _draft.points.length) {
      _selected = null;
    }
    setState(() {});
    await _project();
  }

  int? _nearest(Offset p, {double radius = 24}) {
    int? best;
    for (var i = 0; i < _screen.length; i++) {
      final d = (_screen[i] - p).distance;
      if (d <= radius) {
        radius = d;
        best = i;
      }
    }
    return best;
  }

  int? _segment(Offset p) {
    int? best;
    var distance = 30.0;
    final count = _screen.length - (_draft.polygon ? 0 : 1);
    for (var i = 0; i < count; i++) {
      final a = _screen[i], b = _screen[(i + 1) % _screen.length];
      final d = b - a;
      final t = d.distanceSquared == 0
          ? 0.0
          : (((p - a).dx * d.dx + (p - a).dy * d.dy) / d.distanceSquared).clamp(
              0.0,
              1.0,
            );
      final delta = (p - (a + d * t)).distance;
      if (delta < distance) {
        distance = delta;
        best = i;
      }
    }
    return best;
  }

  Future<void> _tap(Offset p) async {
    final point = await _position(p);
    if (!mounted) return;
    switch (_tool) {
      case DrawTool.pan:
        return;
      case DrawTool.vertex:
        final nearby = _nearest(p);
        if (nearby != null) {
          _selected = nearby;
          break;
        }
        _draft.checkpoint();
        _draft.extend(point, fromStart: _fromStart);
        _selected = _fromStart ? 0 : _draft.points.length - 1;
      case DrawTool.move:
        final nearby = _nearest(p);
        if (nearby != null) {
          _selected = nearby;
        } else if (_selected != null) {
          _draft.checkpoint();
          _draft.points[_selected!] = point;
        }
      case DrawTool.insert:
        final segment = _segment(p);
        if (segment == null) {
          toast(context, 'Tap close to a segment to insert a vertex.');
          return;
        }
        _draft.checkpoint();
        _draft.insert(segment + 1, point);
        _selected = segment + 1;
      case DrawTool.curve:
        if (_curveSegment == null) {
          _curveSegment = _segment(p);
          if (_curveSegment == null) {
            toast(context, 'First tap the segment you want to bend.');
          }
        } else {
          _draft.checkpoint();
          _draft.curve(_curveSegment!, point);
          _curveSegment = null;
        }
      case DrawTool.erase:
        final index = _nearest(p);
        if (index != null) {
          _draft.checkpoint();
          _draft.points.removeAt(index);
          _selected = null;
        }
      case DrawTool.pen:
        toast(context, 'Drag to draw a freehand stroke.');
    }
    await _changed();
  }

  Future<void> _dragStart(Offset p) async {
    _dragging = true;
    if (_tool == DrawTool.pen) {
      _stroke.clear();
      _strokeScreen = [];
      await _dragUpdate(p);
    } else if (_tool == DrawTool.move || _tool == DrawTool.erase) {
      _draft.checkpoint();
      _selected = _nearest(p);
      await _dragUpdate(p);
    }
  }

  Future<void> _dragUpdate(Offset p) async {
    if (!_dragging) return;
    if (_tool == DrawTool.pen) {
      if (_strokeScreen.isNotEmpty && (_strokeScreen.last - p).distance < 5) {
        return;
      }
      if (_stroke.length >= 4000) return;
      _stroke.add(await _position(p));
      setState(() => _strokeScreen.add(p));
    } else if (_tool == DrawTool.move && _selected != null) {
      _draft.points[_selected!] = await _position(p);
      await _changed();
    } else if (_tool == DrawTool.erase) {
      final index = _nearest(p);
      if (index != null) {
        _draft.points.removeAt(index);
        _selected = null;
        await _changed();
      }
    }
  }

  Future<void> _dragEnd() async {
    _dragging = false;
    if (_tool == DrawTool.pen && _stroke.length >= 2) {
      _draft.checkpoint();
      try {
        _draft.addStroke(_stroke, fromStart: _fromStart);
      } finally {
        _stroke.clear();
        _strokeScreen = [];
        await _changed();
      }
    }
  }

  void _choose(DrawTool tool) => _enqueue(() async {
    setState(() {
      _tool = tool;
      _curveSegment = null;
    });
  });
  String get _hint => switch (_tool) {
    DrawTool.pan =>
      'Drag to pan; pinch or scroll to zoom. Choose a tool to edit.',
    DrawTool.vertex =>
      'Tap to add at the ${_fromStart ? "start" : "end"}. Tap a numbered vertex to select it.',
    DrawTool.move => 'Drag a vertex, or select it and tap its new position.',
    DrawTool.insert =>
      'Tap a segment to insert a vertex between its neighbors.',
    DrawTool.curve =>
      _curveSegment == null
          ? 'Tap a segment, then tap away from it to set the curve bend.'
          : 'Tap to place the bend. The curve becomes editable vertices.',
    DrawTool.pen =>
      'Drag along the actual path. Each stroke extends the ${_fromStart ? "start" : "end"}.',
    DrawTool.erase =>
      'Tap or drag over vertices to erase them. Remaining neighbors are joined; check the new path.',
  };
  Future<void> _editCoordinates() async {
    if (_selected == null) return;
    final index = _selected!, point = _draft.points[_selected!];
    final lat = TextEditingController(text: point.latitude.toStringAsFixed(7));
    final lon = TextEditingController(text: point.longitude.toStringAsFixed(7));
    _coordinateControllers.addAll([lat, lon]);
    final result = await showDialog<LatLng>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Vertex ${index + 1} coordinates'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextField(
              controller: lat,
              decoration: const InputDecoration(labelText: 'Latitude'),
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
                signed: true,
              ),
            ),
            TextField(
              controller: lon,
              decoration: const InputDecoration(labelText: 'Longitude'),
              keyboardType: const TextInputType.numberWithOptions(
                decimal: true,
                signed: true,
              ),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              final y = double.tryParse(lat.text),
                  x = double.tryParse(lon.text);
              if (x == null ||
                  y == null ||
                  !x.isFinite ||
                  !y.isFinite ||
                  y.abs() > 90 ||
                  x.abs() > 180) {
                return;
              }
              Navigator.pop(ctx, LatLng(y, x));
            },
            child: const Text('Apply'),
          ),
        ],
      ),
    );
    // Dialog exit animation may still reference its controllers.
    if (result != null && mounted) {
      _draft.checkpoint();
      _draft.points[index] = result;
      await _changed();
    }
  }

  Future<void> _publish() async {
    await _queue;
    if (!mounted || !_draft.canPublish) return;
    setState(() => _publishing = true);
    final saved = await showAppSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => AddPointSheet(
        latLng: _draft.points.first,
        geometry: _draft.geometry,
        initialCategory: widget.polygon
            ? 'no-entry'
            : widget.category ?? 'route-suggestion',
        initialName: widget.name,
        initialComment: widget.comment,
        replaces: widget.replaces,
      ),
    );
    if (!mounted) return;
    if (saved == true) {
      Navigator.pop(context, true);
    } else {
      setState(() => _publishing = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final color = widget.polygon ? const Color(0xFFC62828) : brandGreen;
    final icons = [
      Icons.pan_tool_outlined,
      Icons.add_location_alt_outlined,
      Icons.open_with,
      Icons.add_circle_outline,
      Icons.gesture,
      Icons.edit_outlined,
      Icons.auto_fix_normal,
    ];
    return Scaffold(
      appBar: AppBar(
        title: Text(
          widget.polygon ? 'Draw a no-entry area' : 'Edit a local route',
        ),
        actions: [
          IconButton(
            tooltip: 'Undo',
            icon: const Icon(Icons.undo),
            onPressed: _draft.canUndo
                ? () => _enqueue(() async {
                    _draft.undo();
                    _selected = null;
                    _curveSegment = null;
                    await _changed();
                  })
                : null,
          ),
          IconButton(
            tooltip: 'Redo',
            icon: const Icon(Icons.redo),
            onPressed: _draft.canRedo
                ? () => _enqueue(() async {
                    _draft.redo();
                    _selected = null;
                    _curveSegment = null;
                    await _changed();
                  })
                : null,
          ),
        ],
      ),
      body: SafeArea(
        top: false,
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8),
              child: Wrap(
                alignment: WrapAlignment.center,
                children: [
                  for (final tool in DrawTool.values)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 3),
                      child: ChoiceChip(
                        showCheckmark: false,
                        avatar: Icon(icons[tool.index], size: 18),
                        label: Text(
                          tool.name[0].toUpperCase() + tool.name.substring(1),
                        ),
                        selected: _tool == tool,
                        onSelected: (_) => _choose(tool),
                      ),
                    ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
              child: Text(_hint, style: const TextStyle(fontSize: 12)),
            ),
            Expanded(
              child: Stack(
                fit: StackFit.expand,
                children: [
                  MapLibreMap(
                    styleString: widget.style,
                    compassEnabled: false,
                    trackCameraPosition: true,
                    rotateGesturesEnabled: false,
                    tiltGesturesEnabled: false,
                    initialCameraPosition: CameraPosition(
                      target: widget.center,
                      zoom: 17,
                    ),
                    attributionButtonPosition:
                        AttributionButtonPosition.bottomLeft,
                    onMapCreated: (map) => _map = map,
                    onStyleLoadedCallback: () async {
                      _ready = true;
                      if (_draft.points.length > 1) {
                        final lat = _draft.points.map((p) => p.latitude);
                        final lon = _draft.points.map((p) => p.longitude);
                        await _map!.animateCamera(
                          CameraUpdate.newLatLngBounds(
                            LatLngBounds(
                              southwest: LatLng(
                                lat.reduce(math.min),
                                lon.reduce(math.min),
                              ),
                              northeast: LatLng(
                                lat.reduce(math.max),
                                lon.reduce(math.max),
                              ),
                            ),
                            left: 40,
                            right: 40,
                            top: 40,
                            bottom: 50,
                          ),
                        );
                      }
                      await _project();
                    },
                    onCameraMove: (_) => _project(),
                    onCameraIdle: _project,
                  ),
                  IgnorePointer(
                    child: CustomPaint(
                      painter: _DraftPainter(
                        _screen,
                        _strokeScreen,
                        widget.polygon,
                        color,
                        _selected,
                        _curveSegment,
                      ),
                    ),
                  ),
                  if (_tool != DrawTool.pan)
                    Positioned.fill(
                      bottom: 40,
                      child: PointerInterceptor(
                        intercepting: kIsWeb,
                        child: GestureDetector(
                          excludeFromSemantics: true,
                          behavior: HitTestBehavior.opaque,
                          onTapUp: (d) => _enqueue(() => _tap(d.localPosition)),
                          onPanStart: (d) =>
                              _enqueue(() => _dragStart(d.localPosition)),
                          onPanUpdate: (d) =>
                              _enqueue(() => _dragUpdate(d.localPosition)),
                          onPanEnd: (_) => _enqueue(_dragEnd),
                          onPanCancel: () => _enqueue(_dragEnd),
                        ),
                      ),
                    ),
                  Positioned(
                    right: 8,
                    bottom: 44,
                    child: PointerInterceptor(
                      intercepting: kIsWeb,
                      child: Card(
                        child: Column(
                          children: [
                            IconButton(
                              tooltip: 'Zoom in',
                              icon: const Icon(Icons.add),
                              onPressed: () =>
                                  _map?.animateCamera(CameraUpdate.zoomIn()),
                            ),
                            IconButton(
                              tooltip: 'Zoom out',
                              icon: const Icon(Icons.remove),
                              onPressed: () =>
                                  _map?.animateCamera(CameraUpdate.zoomOut()),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.all(8),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Row(
                    children: [
                      Text('${_draft.points.length}/200 vertices'),
                      IconButton(
                        tooltip: 'Add vertex at map center',
                        icon: const Icon(Icons.add_location_alt_outlined),
                        onPressed: !_ready
                            ? null
                            : () => _enqueue(() async {
                                _draft.checkpoint();
                                _draft.extend(
                                  _map!.cameraPosition?.target ?? widget.center,
                                  fromStart: _fromStart,
                                );
                                _selected = _fromStart
                                    ? 0
                                    : _draft.points.length - 1;
                                await _changed();
                              }),
                      ),
                      const Spacer(),
                      if (!widget.polygon || _tool == DrawTool.pen) ...[
                        const Text('Extend '),
                        DropdownButton<bool>(
                          value: _fromStart,
                          items: const [
                            DropdownMenuItem(value: true, child: Text('Start')),
                            DropdownMenuItem(value: false, child: Text('End')),
                          ],
                          onChanged: (v) =>
                              setState(() => _fromStart = v ?? false),
                        ),
                      ],
                    ],
                  ),
                  Row(
                    children: [
                      Expanded(
                        child: DropdownButton<int>(
                          isExpanded: true,
                          value: _selected,
                          hint: const Text('Select a vertex'),
                          items: [
                            for (var i = 0; i < _draft.points.length; i++)
                              DropdownMenuItem(
                                value: i,
                                child: Text(
                                  'Vertex ${i + 1}${i == 0
                                      ? " · start"
                                      : i == _draft.points.length - 1
                                      ? " · end"
                                      : ""}',
                                ),
                              ),
                          ],
                          onChanged: (i) => setState(() => _selected = i),
                        ),
                      ),
                      IconButton(
                        tooltip: 'Edit coordinates',
                        onPressed: _selected == null ? null : _editCoordinates,
                        icon: const Icon(Icons.edit_location_alt),
                      ),
                      IconButton(
                        tooltip: 'Delete selected vertex',
                        onPressed: _selected == null
                            ? null
                            : () => _enqueue(() async {
                                _draft.checkpoint();
                                _draft.points.removeAt(_selected!);
                                _selected = null;
                                _curveSegment = null;
                                await _changed();
                              }),
                        icon: const Icon(Icons.delete_outline),
                      ),
                    ],
                  ),
                  SizedBox(
                    width: double.infinity,
                    child: FilledButton.icon(
                      style: FilledButton.styleFrom(backgroundColor: color),
                      onPressed: !_draft.canPublish || _publishing
                          ? null
                          : _publish,
                      icon: const Icon(Icons.publish),
                      label: Text(
                        _publishing ? 'Publishing…' : 'Describe & publish',
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _DraftPainter extends CustomPainter {
  final List<Offset> points, stroke;
  final bool polygon;
  final Color color;
  final int? selected, segment;
  _DraftPainter(
    this.points,
    this.stroke,
    this.polygon,
    this.color,
    this.selected,
    this.segment,
  );
  @override
  void paint(Canvas canvas, Size size) {
    void line(List<Offset> pts, bool closed) {
      if (pts.isEmpty) return;
      final path = Path()..moveTo(pts.first.dx, pts.first.dy);
      for (final p in pts.skip(1)) {
        path.lineTo(p.dx, p.dy);
      }
      if (closed && pts.length >= 3) {
        path.close();
        canvas.drawPath(path, Paint()..color = color.withValues(alpha: .22));
      }
      canvas.drawPath(
        path,
        Paint()
          ..color = Colors.white
          ..style = PaintingStyle.stroke
          ..strokeWidth = 7,
      );
      canvas.drawPath(
        path,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 4,
      );
    }

    line(points, polygon);
    line(stroke, false);
    if (segment != null && segment! < points.length) {
      canvas.drawLine(
        points[segment!],
        points[(segment! + 1) % points.length],
        Paint()
          ..color = Colors.amber
          ..strokeWidth = 8,
      );
    }
    for (var i = 0; i < points.length; i++) {
      final p = points[i];
      if (!size.contains(p)) continue;
      canvas.drawCircle(
        p,
        i == selected ? 13 : 10,
        Paint()..color = i == selected ? Colors.amber : Colors.white,
      );
      canvas.drawCircle(
        p,
        i == selected ? 13 : 10,
        Paint()
          ..color = color
          ..style = PaintingStyle.stroke
          ..strokeWidth = 2,
      );
      final text = TextPainter(
        text: TextSpan(
          text: '${i + 1}',
          style: TextStyle(
            color: color,
            fontSize: 10,
            fontWeight: FontWeight.bold,
          ),
        ),
        textDirection: TextDirection.ltr,
      )..layout();
      text.paint(canvas, p - Offset(text.width / 2, text.height / 2));
    }
  }

  @override
  bool shouldRepaint(covariant _DraftPainter oldDelegate) => true;
}
