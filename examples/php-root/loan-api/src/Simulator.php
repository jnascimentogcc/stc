<?php

declare(strict_types=1);

namespace LoanApi;

final class Simulator
{
    /**
     * A fixed-installment (price table) simulation. Money is rounded to two decimals with PHP's
     * round(), which rounds halves away from zero.
     */
    public static function run(float $amount, int $months, float $annualRate): array
    {
        $monthlyRate = $annualRate / 100 / 12;
        $installment = $monthlyRate == 0.0
            ? $amount / $months
            : $amount * $monthlyRate / (1 - (1 + $monthlyRate) ** -$months);

        $installment = round($installment, 2);
        $totalPaid = round($installment * $months, 2);

        return [
            'amount' => $amount,
            'months' => $months,
            'annualRate' => $annualRate,
            'installment' => $installment,
            'totalPaid' => $totalPaid,
            'totalInterest' => round($totalPaid - $amount, 2),
        ];
    }
}
