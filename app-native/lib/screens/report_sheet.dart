import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

import '../api.dart';
import '../theme.dart';
import '../widgets/safety_notice.dart';

/// Walk-audit report form (also handles bike-parking spot feedback when
/// [spotName] is set). Pops with the submit response map on success.
class ReportSheet extends StatefulWidget {
  final LatLng latLng;
  final String? spotName;
  const ReportSheet({super.key, required this.latLng, this.spotName});

  @override
  State<ReportSheet> createState() => _ReportSheetState();
}

class _ReportSheetState extends State<ReportSheet> {
  List<dynamic> _categories = [];
  String _category = 'other';
  final _commentCtl = TextEditingController();
  Uint8List? _photoBytes;
  String? _photoName;
  bool _busy = false;
  String? _error;
  Map<String, dynamic>? _roadInfo;

  bool get isParkingFeedback => widget.spotName != null;

  @override
  void initState() {
    super.initState();
    if (!isParkingFeedback) {
      api.walkAuditCategories().then((cats) {
        if (mounted && cats.isNotEmpty) {
          setState(() {
            _categories = cats;
            _category = cats.first['id'];
          });
        }
      }).catchError((_) {});
      // Show where the report will land before the user submits.
      api
          .roadInfo(widget.latLng.latitude, widget.latLng.longitude)
          .then((info) {
        if (mounted) setState(() => _roadInfo = info);
      }).catchError((_) {});
    }
  }

  Future<void> _pickPhoto(ImageSource source) async {
    final picked = await ImagePicker()
        .pickImage(source: source, maxWidth: 2048, imageQuality: 85);
    if (picked == null) return;
    final bytes = await picked.readAsBytes();
    if (mounted) {
      setState(() {
        _photoBytes = bytes;
        _photoName = picked.name;
      });
    }
  }

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      Map<String, dynamic> result;
      if (isParkingFeedback) {
        await api.submitBikeParkingFeedback(
          spotName: widget.spotName!,
          lat: widget.latLng.latitude,
          lon: widget.latLng.longitude,
          feedback: _commentCtl.text.trim(),
          photoBytes: _photoBytes,
          photoName: _photoName,
        );
        result = {'ok': true};
      } else {
        result = await api.submitWalkAudit(
          category: _category,
          comment: _commentCtl.text.trim(),
          lat: widget.latLng.latitude,
          lon: widget.latLng.longitude,
          photoBytes: _photoBytes,
          photoName: _photoName,
        );
      }
      if (mounted) Navigator.pop(context, result);
    } on Exception catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = e.toString();
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final owner = _roadInfo?['owner'];
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                isParkingFeedback
                    ? 'Feedback: ${widget.spotName}'
                    : 'Report an issue',
                style: Theme.of(context).textTheme.titleLarge,
              ),
              const SizedBox(height: 4),
              Text(
                '${widget.latLng.latitude.toStringAsFixed(5)}, '
                '${widget.latLng.longitude.toStringAsFixed(5)}',
                style: const TextStyle(color: Colors.black54, fontSize: 12),
              ),
              const SizedBox(height: 12),
              if (!isParkingFeedback) ...[
                if (_categories.isNotEmpty)
                  DropdownButtonFormField<String>(
                    initialValue: _category,
                    decoration: const InputDecoration(
                      labelText: 'What kind of issue?',
                      border: OutlineInputBorder(),
                    ),
                    items: [
                      for (final c in _categories)
                        DropdownMenuItem(
                          value: c['id'].toString(),
                          child: Text(c['label'].toString(),
                              overflow: TextOverflow.ellipsis),
                        ),
                    ],
                    onChanged: (v) => setState(() => _category = v ?? 'other'),
                  ),
                const SizedBox(height: 12),
              ],
              TextField(
                controller: _commentCtl,
                minLines: 2,
                maxLines: 5,
                decoration: InputDecoration(
                  labelText: isParkingFeedback
                      ? 'Feedback (condition, capacity…)'
                      : 'Describe the issue',
                  border: const OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              Row(
                children: [
                  OutlinedButton.icon(
                    icon: const Icon(Icons.photo_camera_outlined),
                    label: const Text('Camera'),
                    onPressed:
                        _busy ? null : () => _pickPhoto(ImageSource.camera),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    icon: const Icon(Icons.photo_library_outlined),
                    label: const Text('Gallery'),
                    onPressed:
                        _busy ? null : () => _pickPhoto(ImageSource.gallery),
                  ),
                  const SizedBox(width: 8),
                  if (_photoBytes != null)
                    const Icon(Icons.check_circle, color: brandGreen),
                ],
              ),
              if (owner != null && !isParkingFeedback)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: brandGreen.withValues(alpha: 0.08),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.place_outlined,
                            color: brandGreen, size: 20),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            'Near ${_roadInfo?['name'] ?? 'this road'}, '
                            'maintained by $owner. Your report will appear on '
                            'the BWG map.',
                            style: const TextStyle(fontSize: 13),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(_error!,
                      style: const TextStyle(color: Colors.red)),
                ),
              const Text(routeDisclaimer, style: TextStyle(fontSize: 12)),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: FilledButton.icon(
                  style: FilledButton.styleFrom(backgroundColor: brandGreen),
                  icon: _busy
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(
                              strokeWidth: 2, color: Colors.white))
                      : const Icon(Icons.send),
                  label: Text(_busy ? 'Submitting…' : 'Submit report'),
                  onPressed: _busy ? null : _submit,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
