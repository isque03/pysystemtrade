import numpy as np
import pandas as pd

from sysinit.futures.build_roll_calendars import _matching_prices_from_paired_prices


class TestMatchingPricesFromPairedPrices:
    def test_empty_object_dtype_column_does_not_raise_and_is_excluded(self):
        """
        A contract with zero price rows produces an empty Series that defaults
        to dtype object rather than float64. This used to raise a TypeError
        from np.isnan; it should instead simply be treated as non-matching.
        """
        paired_prices = pd.DataFrame(
            {
                "PRICE": pd.Series([], dtype=object),
                "FORWARD": pd.Series([], dtype=object),
            }
        )

        result = _matching_prices_from_paired_prices(paired_prices)

        assert len(result) == 0

    def test_non_numeric_value_is_excluded_not_treated_as_match(self):
        """
        A stray string (or other non-numeric garbage) in a price column must
        not be silently accepted as a valid match just because it is
        non-null - it should be coerced to NaN and excluded, same as a
        missing price.
        """
        paired_prices = pd.DataFrame(
            {
                "PRICE": [1.0, "not_a_number", 3.0],
                "FORWARD": [1.5, 2.5, 3.5],
            }
        )

        result = _matching_prices_from_paired_prices(paired_prices)

        assert list(result.index) == [0, 2]
        assert 1 not in result.index

    def test_all_nan_row_is_excluded(self):
        paired_prices = pd.DataFrame(
            {
                "PRICE": [1.0, np.nan, 3.0],
                "FORWARD": [1.5, np.nan, 3.5],
            }
        )

        result = _matching_prices_from_paired_prices(paired_prices)

        assert list(result.index) == [0, 2]

    def test_row_with_real_matching_float_values_is_included(self):
        paired_prices = pd.DataFrame(
            {
                "PRICE": [1.0, 2.0, 3.0],
                "FORWARD": [1.5, 2.5, 3.5],
            }
        )

        result = _matching_prices_from_paired_prices(paired_prices)

        assert list(result.index) == [0, 1, 2]
        assert result.all()
