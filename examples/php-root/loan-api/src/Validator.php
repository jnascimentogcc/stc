<?php

declare(strict_types=1);

namespace LoanApi;

final class Validator
{
    public const MAX_AMOUNT = 1000000;
    public const MAX_MONTHS = 60;

    /** The message of the first problem found, or null when the input is valid. */
    public static function check(array $input): ?string
    {
        foreach (['amount', 'months', 'annualRate'] as $field) {
            if (!array_key_exists($field, $input)) {
                return "The field $field is required.";
            }
        }
        if (!is_numeric($input['amount']) || $input['amount'] <= 0) {
            return 'The amount must be a number greater than zero.';
        }
        if ($input['amount'] > self::MAX_AMOUNT) {
            return 'The amount must not exceed 1000000.';
        }
        if (!is_numeric($input['months']) || (int) $input['months'] != $input['months']) {
            return 'The months must be a whole number.';
        }
        if ($input['months'] < 1 || $input['months'] > self::MAX_MONTHS) {
            return 'The months must be between 1 and 60.';
        }
        if (!is_numeric($input['annualRate']) || $input['annualRate'] < 0 || $input['annualRate'] > 100) {
            return 'The annual rate must be between 0 and 100.';
        }

        return null;
    }
}
