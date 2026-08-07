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
          theme: buildTheme(),
          darkTheme: buildDarkTheme(),
          // Follows the device unless Settings says otherwise.
          themeMode: state.themeMode,
          debugShowCheckedModeBanner: false,
          home: const MapScreen(),
        ),
      ),
    ),
  );
}
