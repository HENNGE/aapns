import pytest

from aapns import models


def test_encode():
    notification = models.Notification(
        alert=models.Alert(
            title='Test',
            body='Content',
        )
    )
    assert notification.encode() == b'{"aps":{"alert":{"body":"Content","title":"Test"}}}'


def test_localize():
    notification = models.Notification(
        alert=models.Alert(
            title=models.Localized('Test', ['foo', 'bar']),
            body=models.Localized('Content'),
        )
    )
    assert notification.get_dict() == {
        'aps': {
            'alert': {
                'title-loc-key': 'Test',
                'title-loc-args': ['foo', 'bar'],
                'loc-key': 'Content',
            }
        }
    }


def test_localized_invalid_args():
    with pytest.raises(TypeError):
        models.Localized('foo', [1])


def test_full():
    notification = models.Notification(
        alert=models.Alert(
            title=models.Localized('Test', ['foo', 'bar']),
            body=models.Localized('Content', ['hoge']),
            action_loc_key='action',
            launch_image='my/image.png',
        ),
        badge=13,
        sound='sounds/alert.mp3',
        content_available=True,
        category='my-category',
        thread_id='thread-id',
        extra={
            'myapp': {
                'is': 'awesome'
            }
        }
    )
    assert notification.get_dict() == {
        'aps': {
            'alert': {
                'title-loc-key': 'Test',
                'title-loc-args': ['foo', 'bar'],
                'loc-key': 'Content',
                'loc-args': ['hoge'],
                'action-loc-key': 'action',
                'launch-image': 'my/image.png'
            },
            'badge': 13,
            'sound': 'sounds/alert.mp3',
            'content-available': 1,
            'category': 'my-category',
            'thread-id': 'thread-id',
        },
        'myapp': {
            'is': 'awesome'
        }
    }


def test_invalid_loc_args():
    with pytest.raises(TypeError):
        models.Localized('Test', [1, 2, 3])


def test_invalid_alert_title():
    with pytest.raises(TypeError):
        models.Alert(title=None)
