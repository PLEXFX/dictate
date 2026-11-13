using System.Text.RegularExpressions;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DictateUI;

// The release notes for the version just installed, as one quiet window with a single Close. Port of
// _release_note_groups: headings become group titles, bullets become rows, Markdown marks are removed.
public static class WhatsNewDialog
{
    public static async Task ShowAsync(FrameworkElement anchor)
    {
        var body = new StackPanel { Spacing = 6, MinWidth = 440 };
        foreach (var (heading, entries) in Groups(App.Link.WhatsNewNotes))
        {
            body.Children.Add(new TextBlock { Text = heading, Style = (Style)Application.Current.Resources["BodyStrongTextBlockStyle"], Margin = new Thickness(0, 6, 0, 0) });
            var group = new SettingsGroup();
            foreach (var entry in entries)
                group.Children.Add(new SettingsRow { Title = entry });
            body.Children.Add(group);
        }

        var dialog = new ContentDialog
        {
            XamlRoot = anchor.XamlRoot,
            RequestedTheme = anchor.ActualTheme,
            Title = $"What's new in Dictate {App.Link.Version}",
            Content = new ScrollViewer { Content = body, MaxHeight = 460 },
            CloseButtonText = "Close",
            DefaultButton = ContentDialogButton.Close,
        };
        await dialog.ShowAsync();
    }

    private static List<(string Heading, List<string> Entries)> Groups(string notes)
    {
        var groups = new List<(string, List<string>)>();
        string heading = "Changes in this version";
        var entries = new List<string>();
        foreach (var raw in notes.Split('\n'))
        {
            var line = raw.Trim();
            if (line.Length == 0) continue;
            if (line.StartsWith('#'))
            {
                if (entries.Count > 0) groups.Add((heading, entries));
                heading = line.TrimStart('#').Trim();
                if (heading.Length == 0) heading = "Changes in this version";
                entries = new List<string>();
                continue;
            }
            bool bullet = line.StartsWith("- ") || line.StartsWith("* ");
            if (bullet) line = line[2..].Trim();
            if (line.Length == 0) continue;
            line = Regex.Replace(line, @"\[([^\]]+)\]\([^)]+\)", "$1").Replace("**", "").Replace("__", "").Replace("`", "");
            if (bullet || entries.Count == 0) entries.Add(line);
            else entries[^1] = $"{entries[^1]} {line}";
        }
        if (entries.Count > 0) groups.Add((heading, entries));
        if (groups.Count == 0) groups.Add(("Changes in this version", new List<string> { "Dictate has the latest improvements and fixes." }));
        return groups;
    }
}
