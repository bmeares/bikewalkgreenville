import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:pointer_interceptor/pointer_interceptor.dart';

/// Web needs a real HTML pointer shield across the entire modal barrier.
/// A Flutter barrier alone lets map platform-view clicks fall through.
Future<T?> showAppSheet<T>({
  required BuildContext context,
  required WidgetBuilder builder,
  bool showDragHandle = true,
  bool isScrollControlled = false,
}) {
  if (!kIsWeb) {
    return showModalBottomSheet<T>(
      context: context,
      showDragHandle: showDragHandle,
      isScrollControlled: true,
      useSafeArea: true,
      constraints: BoxConstraints(
        maxWidth: 560,
        maxHeight: MediaQuery.sizeOf(context).height * 0.9,
      ),
      builder: (ctx) => isScrollControlled
          ? builder(ctx) : SingleChildScrollView(child: builder(ctx)),
    );
  }
  return showGeneralDialog<T>(
    context: context,
    barrierDismissible: true,
    barrierLabel: 'Dismiss panel',
    barrierColor: Colors.black54,
    transitionDuration: const Duration(milliseconds: 180),
    pageBuilder: (ctx, animation, secondary) => PointerInterceptor(
      child: Stack(children: [
        Positioned.fill(child: GestureDetector(
          behavior: HitTestBehavior.opaque,
          onTap: () => Navigator.pop(ctx),
          child: const SizedBox.expand(),
        )),
        Align(alignment: Alignment.bottomCenter, child: ConstrainedBox(
          constraints: BoxConstraints(maxWidth: 560,
            maxHeight: MediaQuery.sizeOf(ctx).height * 0.9),
          child: Material(
            color: Theme.of(ctx).colorScheme.surface,
            clipBehavior: Clip.antiAlias,
            borderRadius: const BorderRadius.vertical(top: Radius.circular(24)),
            child: SafeArea(top: false, child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Align(alignment: Alignment.centerRight, child: IconButton(
                  tooltip: 'Close panel', icon: const Icon(Icons.close),
                  onPressed: () => Navigator.pop(ctx),
                )),
                Flexible(child: isScrollControlled ? builder(ctx)
                    : SingleChildScrollView(child: builder(ctx))),
              ],
            )),
          ),
        )),
      ]),
    ),
  );
}

/// Required free-text reason for a public, logged action (rollback, dismiss).
/// Returns null when cancelled. Uses [showAppSheet] so it is web-safe over the map.
Future<String?> askReason(
  BuildContext context, {
  required String title,
  required String body,
  required String action,
}) async {
  final controller = TextEditingController();
  final reason = await showAppSheet<String>(
    context: context,
    builder: (ctx) => Padding(
      padding: const EdgeInsets.all(16),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(title, style: Theme.of(ctx).textTheme.titleMedium),
          const SizedBox(height: 8),
          Text(body),
          const SizedBox(height: 12),
          TextField(
            controller: controller,
            maxLength: 2000,
            maxLines: 3,
            autofocus: true,
            decoration: const InputDecoration(labelText: 'Reason (required)'),
          ),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton(
              onPressed: () {
                final text = controller.text.trim();
                if (text.isNotEmpty) Navigator.pop(ctx, text);
              },
              child: Text(action),
            ),
          ),
        ],
      ),
    ),
  );
  Future<void>.delayed(const Duration(seconds: 1), controller.dispose);
  return reason;
}
