<?php
// Run one occ command whose arguments arrive as a JSON array on stdin, so
// secrets (client secrets, TURN and signaling secrets) never appear in a
// process list. Usage: php /opt/easydeploy/occ-stdin.php < args.json
declare(strict_types=1);

$args = json_decode((string)stream_get_contents(STDIN), true);
if (!is_array($args) || $args === []) {
	fwrite(STDERR, "occ-stdin: expected a JSON array of occ arguments on stdin\n");
	exit(2);
}

$_SERVER['argv'] = array_merge(['occ'], array_map('strval', array_values($args)));
$_SERVER['argc'] = count($_SERVER['argv']);
$argv = $_SERVER['argv'];
$argc = $_SERVER['argc'];

chdir('/var/www/html');
require '/var/www/html/console.php';
