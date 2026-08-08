import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'app_state.dart';
import 'screens/map_screen.dart';
import 'theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(
    ChangeNotifierProvider(
      // Saved travel preferences land a frame or two after first paint; the
      // defaults render fine in the meantime.
      create: (_) => AppState()..load(),
      child: Consumer<AppState>(
        builder: (_, state, child) => MaterialApp(
          title: 'Bike Walk Greenville',
          theme: buildTheme(highContrast: state.highContrast),
          darkTheme: buildDarkTheme(highContrast: state.highContrast),
          // Follows the device unless Settings says otherwise.
          themeMode: state.themeMode,
          debugShowCheckedModeBanner: false,
          // Large UI mode: scale text ~30% past whatever the device already
          // asks for (accessibility settings still win when they ask bigger).
          builder: (context, child) {
            if (!state.largeUi || child == null) return child ?? const SizedBox();
            final mq = MediaQuery.of(context);
            final scale = (mq.textScaler.scale(1.0) * 1.3).clamp(1.3, 2.0);
            return MediaQuery(
              data: mq.copyWith(textScaler: TextScaler.linear(scale)),
              child: child,
            );
          },
          home: const MapScreen(),
        ),
      ),
    ),
  );
}
