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
