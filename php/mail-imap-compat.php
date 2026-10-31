<?php
// Two Mail adjustments the app store build does not make:
// - Stalwart advertises QRESYNC. Horde waits for a CLOSED response the first
//   SELECT never sends, so the folder is never cached.
// - The bundle stamps every follow-up script with btoa(requestToken). That is
//   not the Content-Security-Policy nonce, so the browser blocks the script
//   that draws the mailbox and the page stays white.
// Usage: php /opt/easydeploy/mail-imap-compat.php
declare(strict_types=1);

require_once '/var/www/html/lib/base.php';

$appManager = \OC::$server->get(\OCP\App\IAppManager::class);
if (!$appManager->isInstalled('mail')) {
	fwrite(STDERR, "mail-imap-compat: the mail app is not installed\n");
	exit(3);
}

$appPath = $appManager->getAppPath('mail');
patch_imap_factory($appPath . '/lib/IMAP/IMAPClientFactory.php');
patch_script_nonce($appPath . '/js/mail.js');

function patch_imap_factory(string $path): void {
	$source = read_file($path);
	$marker = "\$params['capability_ignore'] = ['QRESYNC'];";
	if (str_contains($source, $marker)) {
		echo "imap:already\n";
		return;
	}

	$count = 0;
	$updated = preg_replace_callback(
		'/^([ \t]*)if \(\$account->getMailAccount\(\)->getAuthMethod\(\) === \'xoauth2\'\) \{/m',
		static function (array $match): string {
			$pad = $match[1];
			return $pad . "// Stalwart advertises QRESYNC. Horde waits for a CLOSED response\n"
				. $pad . "// the first SELECT does not send, so the folder is never cached and\n"
				. $pad . "// /apps/mail/api/messages stays HTTP 400.\n"
				. $pad . "\$params['capability_ignore'] = ['QRESYNC'];\n\n"
				. $match[0];
		},
		$source,
		1,
		$count
	);
	if ($count !== 1 || !is_string($updated) || !str_contains($updated, $marker)) {
		fwrite(STDERR, "mail-imap-compat: {$path} has no xoauth2 hook; Mail changed and the patch needs an update\n");
		exit(5);
	}
	write_file($path, $updated);
	echo "imap:patched\n";
}

function patch_script_nonce(string $path): void {
	$source = read_file($path);
	$marker = 'meta[name="csp-nonce"]';
	if (str_contains($source, $marker)) {
		echo "nonce:already\n";
		return;
	}

	$needle = 'n.nc=btoa((0,e.do)())';
	if (!str_contains($source, $needle)) {
		fwrite(STDERR, "mail-imap-compat: {$path} has no webpack nonce hook; Mail changed and the patch needs an update\n");
		exit(7);
	}
	$replacement = 'n.nc=(document.querySelector(\'meta[name="csp-nonce"]\')||{}).nonce||""';
	$updated = str_replace($needle, $replacement, $source);
	if (!str_contains($updated, $marker)) {
		fwrite(STDERR, "mail-imap-compat: could not rewrite the webpack nonce in {$path}\n");
		exit(7);
	}
	write_file($path, $updated);
	echo "nonce:patched\n";
}

function read_file(string $path): string {
	$source = file_get_contents($path);
	if ($source === false) {
		fwrite(STDERR, "mail-imap-compat: cannot read {$path}\n");
		exit(4);
	}
	return $source;
}

function write_file(string $path, string $contents): void {
	if (file_put_contents($path, $contents) === false) {
		fwrite(STDERR, "mail-imap-compat: cannot write {$path}\n");
		exit(6);
	}
	if (function_exists('opcache_invalidate')) {
		opcache_invalidate($path, true);
	}
}
