from __future__ import absolute_import
from multiprocessing import Value, Lock
from collections import deque
from enum import Enum
import logging

log = logging.getLogger(__name__)


class RollingNumber(object):
    ''' A number which can be used to track counters (increment) or set values
        over time.

    It is "rolling" in the sense that a 'milliseconds' is given that you
    want to track (such as 10 seconds) and then that is broken into
    buckets (defaults to 10) so that the 10 second window doesn't empty
    out and restart every 10 seconds, but instead every 1 second you have
    a new bucket added and one dropped so that 9 of the buckets remain
    and only the newest starts from scratch.

    This is done so that the statistics are gathered over a rolling 10
    second window with data being added/dropped in 1 second intervals
    (or whatever granularity is defined by the arguments) rather than
    each 10 second window starting at 0 again.
    '''

    def __init__(self, time, milliseconds, bucket_numbers):
        self.time = time
        self.milliseconds = milliseconds
        self.buckets = BucketCircular(bucket_numbers)
        self.bucket_numbers = bucket_numbers
        self.cumulative_sum = CumulativeSum()

        if self.milliseconds % self.bucket_numbers != 0:
            raise Exception('The milliseconds must divide equally into '
                            'bucket_numbers. For example 1000/10 is ok, '
                            '1000/11 is not.')

    def buckets_size_in_milliseconds(self):
        return self.milliseconds / self.bucket_numbers

    def increment(self, event):
        self.current_bucket().adder(event).increment()

    def current_bucket(self):
        current_time = self.time.current_time_in_millis()
        current_bucket = self.buckets.peek_last()

        if current_bucket is not None and current_time < current_bucket.window_start + self.time.current_time_in_millis():
            return current_bucket

        # If we didn't find the current bucket above, then we have to
        # create one.
        if not self.buckets.peek_last():
            new_bucket = Bucket(current_time)
            self.buckets.add_last(new_bucket)
            return new_bucket
        else:
            for i in range(self.bucket_numbers):
                last_bucket = self.buckets.peek_last()
                time = last_bucket.window_start + self.buckets_size_in_milliseconds()
                if current_time < time:
                    return last_bucket
                elif current_time - time > self.milliseconds:
                    self.reset()
                    return self.current_bucket()
                else:
                    self.buckets.add_last(Bucket(time))
                    self.cumulative_sum.add_bucket(last_bucket)

            return self.buckets.peek_last()

    def reset(self):
        last_bucket = self.buckets.peek_last()
        if last_bucket:
            self.cumulative_sum.add_bucket(last_bucket)

        self.buckets.clear()


class BucketCircular(deque):
    ''' This is a circular array acting as a FIFO queue. '''

    def __init__(self, size):
        super(BucketCircular, self).__init__(maxlen=size)

    @property
    def size(self):
        return len(self)

    def get_last(self):
        return self.peek_last()

    def peek_last(self):
        try:
            return self[-1]
        except IndexError:
            return None

    def add_last(self, bucket):
        self.appendleft(bucket)


class Bucket(object):
    ''' Counters for a given 'bucket' of time. '''

    def __init__(self, start_time):
        self.window_start = start_time
        self._adder = {}
        self._max_updater = {}

        # TODO: Change this to use a metaclass
        for name, event in RollingNumberEvent.__members__.items():
            if event.is_counter():
                self._adder[name] = LongAdder()
                continue

            if event.is_max_updater():
                self._max_updater[name] = LongMaxUpdater()

    def get(self, event):
        if event.is_counter():
            return self.adder(event).sum()

        if event.is_max_updater():
            return self.max_updater(event).max()

        raise Exception('Unknown type of event.')

    def adder(self, event):
        if event.is_counter():
            return self._adder[event.name]

        raise Exception('Unknown type of event.')

    def max_updater(self, event):
        if event.is_max_updater():
            return self._max_updater[event.name]

        raise Exception('Unknown type of event.')


class Counter(object):
    def __init__(self, min_value=0):
        self.count = Value('i', min_value)
        self.lock = Lock()

    def increment(self):
        with self.lock:
            self.count.value += 1

    def decrement(self):
        with self.lock:
            self.count.value -= 1


class LongAdder(Counter):

    def sum(self):
        with self.lock:
            return self.count.value

    def add(self, value):
        with self.lock:
            self.count.value += value


class LongMaxUpdater(Counter):

    def max(self):
        with self.lock:
            return self.count.value

    def update(self, value):
        with self.lock:
            self.count.value = value


class CumulativeSum(object):

    def __init__(self):
        self._adder = {}
        self._max_updater = {}

        # TODO: Change this to use a metaclass
        for name, event in RollingNumberEvent.__members__.items():
            if event.is_counter():
                self._adder[name] = LongAdder()
                continue

            if event.is_max_updater():
                self._max_updater[name] = LongMaxUpdater()

    def add_bucket(self, bucket):
        for name, event in RollingNumberEvent.__members__.items():
            if event.is_counter():
                self.adder(event).add(bucket.adder(event).sum())

            if event.is_max_updater():
                self.max_updater(event).update(bucket.max_updater(event).max())

    def get(self, event):
        if event.is_counter():
            return self.adder(event).sum()

        if event.is_max_updater():
            return self.max_updater(event).max()

        raise Exception('Unknown type of event.')

    def adder(self, event):
        if event.is_counter():
            return self._adder[event.name]

        raise Exception('Unknown type of event.')

    def max_updater(self, event):
        if event.is_max_updater():
            return self._max_updater[event.name]

        raise Exception('Unknown type of event.')


class RollingNumberEvent(Enum):
    ''' Various states/events that can be captured in the RollingNumber.

    Note that events are defined as different types:

    * Counter: is_counter() == true
    * MaxUpdater: is_max_updater() == true

    The Counter type events can be used with RollingNumber#increment,
    RollingNumber#add, RollingNumber#getRollingSum} and others.

    The MaxUpdater type events can be used with RollingNumber#updateRollingMax
    and RollingNumber#getRollingMaxValue.
    '''

    SUCCESS = 1
    FAILURE = 1
    TIMEOUT = 1
    SHORT_CIRCUITED = 1
    THREAD_POOL_REJECTED = 1
    SEMAPHORE_REJECTED = 1
    FALLBACK_SUCCESS = 1
    FALLBACK_FAILURE = 1
    FALLBACK_REJECTION = 1
    EXCEPTION_THROWN = 1
    THREAD_EXECUTION = 1
    THREAD_MAX_ACTIVE = 2
    COLLAPSED = 1
    RESPONSE_FROM_CACHE = 1

    def __init__(self, event_type):
        self.event_type = event_type

    def is_counter(self):
        return self.event_type == 1

    def is_max_updater(self):
        return self.event_type == 2
