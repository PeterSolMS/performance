﻿// Licensed to the .NET Foundation under one or more agreements.
// The .NET Foundation licenses this file to you under the MIT license.
// See the LICENSE file in the project root for more information.

using BenchmarkDotNet.Attributes;
using Microsoft.Extensions.DependencyInjection;
using MicroBenchmarks;

namespace Microsoft.Extensions.Logging
{
    [BenchmarkCategory(Categories.Libraries)]
    public class ScopesOverheadBenchmark: LoggingBenchmarkBase
    {
        private ILogger _logger;

        [Params(true, false)]
        public bool HasISupportLoggingScopeLogger { get; set; } = false;

        [Params(true, false)]
        public bool CaptureScopes { get; set; } = false;

        // Baseline as this is the fastest way to do nothing
        [Benchmark(Baseline = true)]
        public void FilteredByLevel()
        {
            TwoArgumentTraceMessage(_logger, 1, "string", Exception);
        }

        [Benchmark]
        public void FilteredByLevel_InsideScope()
        {
            using (_logger.BeginScope("string"))
            {
                TwoArgumentTraceMessage(_logger, 1, "string", Exception);
            }
        }

        [Benchmark]
        public void NotFiltered()
        {
            TwoArgumentErrorMessage(_logger, 1, "string", Exception);
        }

        [Benchmark]
        public void NotFiltered_InsideScope()
        {
            using (_logger.BeginScope("string"))
            {
                TwoArgumentErrorMessage(_logger, 1, "string", Exception);
            }
        }

        [GlobalSetup]
        public void Setup()
        {
            var services = new ServiceCollection();
            services.AddLogging();
            if (HasISupportLoggingScopeLogger)
            {
                services.AddSingleton<ILoggerProvider, LoggerProviderWithISupportExternalScope>();
            }
            else
            {
                services.AddSingleton<ILoggerProvider, LoggerProvider<NoopLogger>>();
            }

            services.Configure<LoggerFilterOptions>(options => options.CaptureScopes = CaptureScopes);

            _logger = services.BuildServiceProvider().GetService<ILoggerFactory>().CreateLogger("Logger");
        }

        class LoggerProviderWithISupportExternalScope: LoggerProvider<NoopLogger>, ISupportExternalScope
        {
            public void SetScopeProvider(IExternalScopeProvider scopeProvider)
            {
            }
        }
    }
}