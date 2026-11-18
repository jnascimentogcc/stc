<?php

declare(strict_types=1);

require __DIR__ . '/../src/App.php';
require __DIR__ . '/../src/Validator.php';
require __DIR__ . '/../src/Simulator.php';

use LoanApi\App;
use LoanApi\Simulator;
use LoanApi\Validator;

$app = new App();

$app->get('/health', fn (array $body): array => [200, ['status' => 'UP']]);

$app->post('/simulations', function (array $input): array {
    $error = Validator::check($input);
    if ($error !== null) {
        return [422, ['error' => $error]];
    }

    // PHP's loose typing: a numeric string such as "1000" is accepted for every number.
    return [200, Simulator::run((float) $input['amount'], (int) $input['months'], (float) $input['annualRate'])];
});

$app->run();
