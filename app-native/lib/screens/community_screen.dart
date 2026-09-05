import 'package:flutter/material.dart';
import '../api.dart';
import '../widgets/safety_notice.dart';

class CommunityScreen extends StatefulWidget {
  const CommunityScreen({super.key});
  @override
  State<CommunityScreen> createState() => _CommunityScreenState();
}

class _CommunityScreenState extends State<CommunityScreen> {
  late Future<List<Map<String, dynamic>>> _history = api.communityHistory();
  String? _busy;

  Future<void> _rollback(Map<String, dynamic> row) async {
    final reason = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Roll back this contribution?'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Text(
              'This removes it from the community map and routing. The original and your reason stay in public history.',
            ),
            const SizedBox(height: 12),
            TextField(
              controller: reason,
              maxLength: 2000,
              maxLines: 3,
              decoration: const InputDecoration(labelText: 'Reason (required)'),
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () {
              if (reason.text.trim().isNotEmpty) Navigator.pop(ctx, true);
            },
            child: const Text('Roll back'),
          ),
        ],
      ),
    );
    final explanation = reason.text.trim();
    Future<void>.delayed(const Duration(seconds: 1), reason.dispose);
    if (confirmed != true || !mounted) return;
    setState(() => _busy = row['id']);
    try {
      await api.rollbackContribution(row['id'], explanation);
      if (mounted) setState(() => _history = api.communityHistory());
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Could not roll back. Refresh and try again.'),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Community map'),
      actions: [
        IconButton(
          tooltip: 'Refresh history',
          icon: const Icon(Icons.refresh),
          onPressed: () => setState(() => _history = api.communityHistory()),
        ),
      ],
    ),
    body: FutureBuilder<List<Map<String, dynamic>>>(
      future: _history,
      builder: (ctx, snapshot) {
        if (snapshot.hasError) {
          return const Center(
            child: Text('History could not load. Tap refresh to retry.'),
          );
        }
        if (!snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }
        return ListView(
          children: [
            const SafetyNotice(),
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text(
                'A shared map for biking, walking, rolling, and transit in Greenville. '
                'Tap the map to add a place, correct information, or draw a local route. '
                'Changes publish immediately. Roll back inaccurate contributions with a public reason.',
              ),
            ),
            if (snapshot.data!.isEmpty)
              const ListTile(
                title: Text('Be the first to share local knowledge.'),
              ),
            for (final row in snapshot.data!)
              ListTile(
                isThreeLine: true,
                leading: Icon(
                  row['active'] == true ? Icons.public : Icons.history,
                ),
                title: Text(
                  row['name']?.toString().isNotEmpty == true
                      ? row['name']
                      : row['category'] ?? 'Contribution',
                ),
                subtitle: Text(
                  '${row['comment'] ?? ''}\n${row['ts'] ?? ''} · ${row['active'] == true ? 'Published' : 'History'}',
                ),
                trailing: row['active'] == true
                    ? IconButton(
                        tooltip: 'Roll back contribution',
                        icon: _busy == row['id']
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(),
                              )
                            : const Icon(Icons.undo),
                        onPressed: _busy == null ? () => _rollback(row) : null,
                      )
                    : null,
              ),
          ],
        );
      },
    ),
  );
}
