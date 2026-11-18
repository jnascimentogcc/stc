<?php

declare(strict_types=1);

namespace LoanApi;

/** A minimal Slim-style router: `$app->get('/path', fn (array $body): array => [...])`. */
final class App
{
    /** @var array<string, array<string, callable>> */
    private array $routes = [];

    public function get(string $path, callable $handler): void
    {
        $this->routes['GET'][$path] = $handler;
    }

    public function post(string $path, callable $handler): void
    {
        $this->routes['POST'][$path] = $handler;
    }

    /** Handlers return [status, body]; a missing route is 404 and a wrong method is 405. */
    public function run(): never
    {
        $method = $_SERVER['REQUEST_METHOD'];
        $path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);

        $handler = $this->routes[$method][$path] ?? null;
        if ($handler === null) {
            $known = array_filter($this->routes, fn (array $paths): bool => isset($paths[$path]));
            [$status, $body] = $known === [] ? [404, ['error' => 'Not found.']] : [405, ['error' => 'Use POST.']];
        } else {
            $input = json_decode(file_get_contents('php://input') ?: '{}', true);
            [$status, $body] = is_array($input)
                ? $handler($input)
                : [400, ['error' => 'The body must be a JSON object.']];
        }

        http_response_code($status);
        header('Content-Type: application/json');
        echo json_encode($body);
        exit;
    }
}
