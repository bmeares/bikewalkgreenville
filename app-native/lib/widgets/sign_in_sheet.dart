import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api.dart';
import '../auth.dart';

/// Passwordless sign-in: email → 6-digit code → signed in. Pops `true` when
/// it completes; dismissing it pops null.
class SignInSheet extends StatefulWidget {
  const SignInSheet({super.key});
  @override
  State<SignInSheet> createState() => _SignInSheetState();
}

class _SignInSheetState extends State<SignInSheet> {
  static const resendAfter = 30;
  final _emailCtl = TextEditingController();
  final _codeCtl = TextEditingController();
  bool _codeSent = false;
  bool _busy = false;
  String? _error;
  int _resendIn = 0;
  Timer? _tick;

  @override
  void dispose() {
    _tick?.cancel();
    _emailCtl.dispose();
    _codeCtl.dispose();
    super.dispose();
  }

  String _message(Object e, {required bool verifying}) {
    if (e is ApiError) {
      if (e.status == 429) return 'Too many codes — wait a few minutes.';
      if (e.status == 401 && verifying) return 'That code is wrong or expired.';
      return e.message;
    }
    return 'Could not reach Bike Walk Greenville. Check your connection.';
  }

  Future<void> _send() async {
    final email = _emailCtl.text.trim().toLowerCase();
    if (!RegExp(r'^[^@\s]+@[^@\s]+\.[^@\s]+$').hasMatch(email)) {
      setState(() => _error = 'Enter a valid email address.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await api.requestCode(email);
      if (!mounted) return;
      _codeCtl.clear();
      setState(() {
        _codeSent = true;
        _resendIn = resendAfter;
      });
      _tick?.cancel();
      _tick = Timer.periodic(const Duration(seconds: 1), (t) {
        if (!mounted || _resendIn <= 1) t.cancel();
        if (mounted) setState(() => _resendIn = (_resendIn - 1).clamp(0, 99));
      });
    } catch (e) {
      if (mounted) setState(() => _error = _message(e, verifying: false));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _verify() async {
    final code = _codeCtl.text.trim();
    if (!RegExp(r'^\d{6}$').hasMatch(code)) {
      setState(() => _error = 'Enter the 6-digit code from the email.');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final verified = await api.verifyCode(
        _emailCtl.text.trim().toLowerCase(),
        code,
      );
      await auth.completeSignIn(verified);
      if (mounted) Navigator.pop(context, true);
    } catch (e) {
      if (mounted) {
        setState(() {
          _busy = false;
          _error = _message(e, verifying: true);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: SingleChildScrollView(
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
        child: AutofillGroup(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Semantics(
                header: true,
                child: Text('Sign in', style: theme.textTheme.titleLarge),
              ),
              const SizedBox(height: 6),
              const Text(
                'Public contributions carry a verified email so BWG can '
                'moderate the map. Browsing and routing never need an account.',
              ),
              const SizedBox(height: 12),
              TextField(
                key: const ValueKey('sign-in-email'),
                controller: _emailCtl,
                enabled: !_codeSent && !_busy,
                keyboardType: TextInputType.emailAddress,
                autofillHints: const [AutofillHints.email],
                autocorrect: false,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _send(),
                decoration: const InputDecoration(labelText: 'Email'),
              ),
              if (_codeSent) ...[
                const SizedBox(height: 8),
                Semantics(
                  liveRegion: true,
                  child: Text(
                    'We emailed a 6-digit code to ${_emailCtl.text.trim()}. '
                    'It works for 10 minutes.',
                    style: theme.textTheme.bodySmall,
                  ),
                ),
                const SizedBox(height: 8),
                TextField(
                  key: const ValueKey('sign-in-code'),
                  controller: _codeCtl,
                  autofocus: true,
                  enabled: !_busy,
                  keyboardType: TextInputType.number,
                  autofillHints: const [AutofillHints.oneTimeCode],
                  inputFormatters: [
                    FilteringTextInputFormatter.digitsOnly,
                    LengthLimitingTextInputFormatter(6),
                  ],
                  textInputAction: TextInputAction.done,
                  onSubmitted: (_) => _verify(),
                  style: const TextStyle(fontSize: 22, letterSpacing: 8),
                  decoration: const InputDecoration(labelText: 'Code'),
                ),
              ],
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Semantics(
                    liveRegion: true,
                    child: Text(
                      _error!,
                      style: TextStyle(color: theme.colorScheme.error),
                    ),
                  ),
                ),
              const SizedBox(height: 12),
              if (!_codeSent)
                FilledButton(
                  onPressed: _busy ? null : _send,
                  child: _busy
                      ? _spinner('Sending code')
                      : const Text('Send code'),
                )
              else ...[
                FilledButton(
                  onPressed: _busy ? null : _verify,
                  child: _busy ? _spinner('Verifying') : const Text('Verify'),
                ),
                // Wrap, not Row + Spacer: at large text the two stack.
                Wrap(
                  alignment: WrapAlignment.spaceBetween,
                  children: [
                    TextButton(
                      onPressed: _busy
                          ? null
                          : () => setState(() {
                              _codeSent = false;
                              _error = null;
                              _tick?.cancel();
                            }),
                      child: const Text('Change email'),
                    ),
                    TextButton(
                      onPressed: _busy || _resendIn > 0 ? null : _send,
                      child: Text(
                        _resendIn > 0 ? 'Resend in $_resendIn s' : 'Resend',
                      ),
                    ),
                  ],
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  /// Stands in for the button label while busy, so it carries one.
  Widget _spinner(String label) => SizedBox(
    width: 18,
    height: 18,
    child: CircularProgressIndicator(strokeWidth: 2, semanticsLabel: label),
  );
}
