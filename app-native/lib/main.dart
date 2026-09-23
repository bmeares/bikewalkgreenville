import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'app_state.dart';
import 'auth.dart';
import 'group_ride.dart';
import 'rides.dart';
import 'screens/map_screen.dart';
import 'theme.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  // Saved travel preferences land a frame or two after first paint; the
  // defaults render fine in the meantime. The account loads alongside and
  // merges its synced settings once both are in.
  final appState = AppState()..load();
  auth
    ..attach(appState)
    ..load();
  runApp(
    MultiProvider(
      providers: [
        ChangeNotifierProvider.value(value: appState),
        ChangeNotifierProvider.value(value: auth),
        ChangeNotifierProvider(create: (_) => RideRecorder()..load()),
        // Not lazy: a ride saved before the app closed resumes at launch.
        ChangeNotifierProvider(
          create: (_) => GroupRideClient()..load(),
          lazy: false,
        ),
      ],
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
            if (!state.largeUi || child == null) {
              return child ?? const SizedBox();
            }
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
