// Licensed to the .NET Foundation under one or more agreements.
// The .NET Foundation licenses this file to you under the MIT license.
// See the LICENSE file in the project root for more information.
//

using BenchmarkDotNet.Attributes;
using MicroBenchmarks;

namespace Benchstone.BenchI
{
[BenchmarkCategory(Categories.Runtime, Categories.Benchstones, Categories.JIT, Categories.BenchI)]
public class BubbleSort
{
    static void SortArray(int[] tab, int last) {
        bool swap;
        int temp;
        do {
            swap = false;
            for (int i = 0; i < last; i++) {
                if (tab[i] > tab[i + 1]) {
                    temp = tab[i];
                    tab[i] = tab[i + 1];
                    tab[i + 1] = temp;
                    swap = true;
                }
            }
        }
        while (swap);
    }

    static bool VerifySort(int[] tab, int last) {
        for (int i = 0; i < last; i++) {
            if (tab[i] > tab[i + 1]) {
                return false;
            }
        }

        return true;
    }

    // this benchmark is BAD, it should not allocate the array and check the order, but I am porting "as is"
    [Benchmark(Description = nameof(BubbleSort))]
    public bool Test() {
        int[] tab = new int[100];
        int k = 0;
        for (int i = 9; i >= 0; i--) {
            for (int j = i * 10; j < (i + 1) * 10; j++) {
                tab[k++] = ((j & 1) == 1) ? j + 1 : j - 1;
            }
        }
        SortArray(tab, 99);
        bool result = VerifySort(tab, 99);
        return result;
    }
}
}
