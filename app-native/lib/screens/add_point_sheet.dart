import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

import '../api.dart';
import '../theme.dart';

/// One submittable kind of missing point. Ids must match the server's
/// SUBMISSION_CATEGORIES in `plugins/map-layers.py`.
class _PointCategory {
  final String id;
  final String label;
  final IconData icon;
  const _PointCategory(this.id, this.label, this.icon);
}

const _categories = <_PointCategory>[
  _PointCategory('bike-parking', 'Bike parking', Icons.local_parking),
  _PointCategory('repair-station', 'Repair station', Icons.build),
  _PointCategory('water-fountain', 'Water fountain', Icons.water_drop),
  _PointCategory('bike-business', 'Bike friendly business', Icons.storefront),
  _PointCategory('other', 'Something else', Icons.place),
];

/// "This exists on the ground but not on the map" — a rider submits a missing
/// bike rack, repair station, etc. Anonymous on purpose: no accounts in this
/// app (yet); submissions are reviewed by hand before they go anywhere.
class AddPointSheet extends StatefulWidget {
  final LatLng latLng;
  const AddPointSheet({super.key, required this.latLng});

  @override
  State<AddPointSheet> createState() => _AddPointSheetState();
}

class _AddPointSheetState extends State<AddPointSheet> {
  String _category = _categories.first.id;
  final _nameCtl = TextEditingController();
  final _commentCtl = TextEditingController();
  Uint8List? _photoBytes;
  String? _photoName;
  bool _busy = false;
  String? _error;

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
    if (_nameCtl.text.trim().isEmpty && _commentCtl.text.trim().isEmpty) {
      setState(() =>
          _error = 'Give the spot a name or a short description.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await api.submitPoint(
        category: _category,
        name: _nameCtl.text.trim(),
        comment: _commentCtl.text.trim(),
        lat: widget.latLng.latitude,
        lon: widget.latLng.longitude,
        photoBytes: _photoBytes,
        photoName: _photoName,
      );
      if (mounted) Navigator.pop(context, true);
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
              Text('Add a missing place',
                  style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 4),
              Text(
                'On the ground but not on the map? Tell us and we\'ll add it. '
                '(${widget.latLng.latitude.toStringAsFixed(5)}, '
                '${widget.latLng.longitude.toStringAsFixed(5)})',
                style: const TextStyle(color: Colors.black54, fontSize: 12),
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final c in _categories)
                    ChoiceChip(
                      avatar: Icon(c.icon, size: 18),
                      label: Text(c.label),
                      selected: _category == c.id,
                      onSelected: (_) => setState(() => _category = c.id),
                    ),
                ],
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _nameCtl,
                decoration: const InputDecoration(
                  labelText: 'Name (e.g. "Rack outside the library")',
                  border: OutlineInputBorder(),
                ),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _commentCtl,
                minLines: 2,
                maxLines: 4,
                decoration: const InputDecoration(
                  labelText: 'Anything else worth knowing?',
                  border: OutlineInputBorder(),
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
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child:
                      Text(_error!, style: const TextStyle(color: Colors.red)),
                ),
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
                      : const Icon(Icons.add_location_alt),
                  label: Text(_busy ? 'Submitting…' : 'Submit'),
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
