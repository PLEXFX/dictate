using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Microsoft.UI.Xaml.Media.Animation;
using Microsoft.UI.Xaml.Navigation;
using Windows.System;

namespace DictateUI;

// The word list opens in place of the Dictation page (not a second window) and saves as you type.
public sealed partial class VocabularyPage : Page
{
    private bool _loading;

    public VocabularyPage()
    {
        InitializeComponent();
        KeyDown += OnKeyDown;
        // Focus goes to the list once the page is on screen, so typing starts at once and Space cannot press Back.
        Loaded += (_, _) => Editor.Focus(FocusState.Programmatic);
    }

    private List<string> Words => Settings.CleanVocabulary(Editor.Text.Split('\r', '\n'));

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        _loading = true;
        Editor.Text = string.Join("\r", App.Link.Settings.Vocabulary);
        _loading = false;
        RefreshNote();
    }

    private void RefreshNote()
    {
        int n = Words.Count;
        Note.Text = $"{n} {(n == 1 ? "entry" : "entries")} · Up to {Settings.MaxVocabularyWords} · Changes save automatically";
    }

    private void OnTextChanged(object sender, TextChangedEventArgs e)
    {
        RefreshNote();
        if (_loading) return;
        App.Link.Settings.Vocabulary = Words;
        App.QueueSave();
    }

    private void OnKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key != VirtualKey.Escape) return;
        e.Handled = true;
        OnBack(null, null);
    }

    private void OnBack(object sender, RoutedEventArgs e) =>
        Frame.Navigate(typeof(DictationPage), null, new SuppressNavigationTransitionInfo());
}
