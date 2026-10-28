def find_max(nums):
    """Return the largest number in a non-empty list."""
    best = nums[0]
    for i in range(len(nums)):       # FIXED: iterate over all elements
        if nums[i] > best:
            best = nums[i]
    return best


def sum_range(start, end):
    """Return the sum of integers from start to end inclusive."""
    total = 0
    for i in range(start, end + 1):  # FIXED: include end
        total += i
    return total
